#!/usr/bin/env bash
# The CI tests, the same on GitHub Actions, GitLab CI and a developer's machine (#765).
#
#   scripts/ci/test.sh setup   uv and bun at the versions in api/pyproject.toml and
#                              web/package.json (nothing to do when PATH has them)
#   scripts/ci/test.sh api     ruff, migrations and pytest for api/
#   scripts/ci/test.sh web     API types, type check, lint, formatting, vitest, build
#   scripts/ci/test.sh e2e     the app on the mock Nautobot, then Playwright in Chromium
#   scripts/ci/test.sh all     all of them; fails when any part did
#   scripts/ci/test.sh pytest [args]   only pytest for api/, e.g. `pytest tests/test_board_once.py
#                              -k cache`: for while you work (#776)
#
# Every check of a part runs even after one fails; the part then fails.  PostgreSQL:
# TEST_PG_URL (postgresql://user:password@host:port) or a Docker container.  Logs of
# the e2e app and mock: app.log and mock.log in the repository root.
set -uo pipefail

# shellcheck source=scripts/ci/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# A check the rest of the part needs: stop the part when it fails.
need() {
  check "$@" || { report "$PART"; exit 1; }
}

part_setup() {
  PART=Setup
  check "uv $(uv_version)" ensure_uv
  check "bun $(bun_version)" ensure_bun
  report "$PART"
}

part_api() {
  PART=API
  trap cleanup EXIT
  cd "$API_DIR" || exit 1
  need "uv $(uv_version)" ensure_uv
  need "Install" uv sync --locked
  need "PostgreSQL" start_postgres
  need "Test database" pg_database api
  load_local_env
  use_test_services
  export DATABASE_URL="$PG_DATABASE_URL" API_TEST_DATABASE_URL="$PG_DATABASE_URL"
  check "Ruff lint" ruff_check
  check "Ruff format" uv run ruff format --check --diff .
  check "Migrations apply" uv run alembic upgrade head
  check "Tests" uv run pytest -v
  report "$PART"
}

# Only pytest, with what the api part gives it (a throwaway database, the test settings).
part_pytest() {
  PART=Pytest
  trap cleanup EXIT
  cd "$API_DIR" || exit 1
  need "uv $(uv_version)" ensure_uv
  need "Install" uv sync --locked --quiet
  need "PostgreSQL" start_postgres
  need "Test database" pg_database pytest
  load_local_env
  use_test_services
  export DATABASE_URL="$PG_DATABASE_URL" API_TEST_DATABASE_URL="$PG_DATABASE_URL"
  check "Tests" uv run pytest "$@"
  report "$PART"
}

web_install() {
  # Install scripts don't run; `nuxt prepare` makes the generated types.  --bun
  # everywhere: the tools run on bun, as in the image (#635).
  bun install --frozen-lockfile --ignore-scripts && bun --bun x nuxt prepare
}

web_types_current() {
  # The api part checks openapi.json is the API's spec.
  bun --bun run api:generate && git diff --exit-code api/api.schema.ts
}

part_web() {
  PART=Web
  cd "$WEB_DIR" || exit 1
  need "bun $(bun_version)" ensure_bun
  need "Install" web_install
  check "API types match web/api/openapi.json" web_types_current
  check "Types" bun --bun run type:check
  check "Lint" bun --bun run lint
  check "Formatting" bun --bun run format:check
  check "Unit tests" bun --bun run test
  check "Static build (no inline scripts, #594)" bun --bun run generate
  report "$PART"
}

e2e_install() {
  (cd "$API_DIR" && uv sync --locked --group e2e)
}

e2e_chromium() {
  (cd "$API_DIR" && install_chromium)
}

e2e_pages() {
  (cd "$WEB_DIR" && bun install --frozen-lockfile --ignore-scripts && bun --bun run generate)
}

e2e_start() {
  local mock_port app_port
  mock_port="$(free_port)" && app_port="$(free_port)" || return 1
  export NAUTOBOT_URL="http://127.0.0.1:$mock_port" NAUTOBOT_TOKEN=demo-token
  export NAUTOBOT_MAPS_DATABASE_URL="$PG_DATABASE_URL" FLASK_RUN_PORT="$app_port"
  export E2E_BASE_URL="http://127.0.0.1:$app_port"
  MOCK_HOST=127.0.0.1 MOCK_PORT="$mock_port" \
    "$API_DIR/.venv/bin/python" "$ROOT/demo/mock_nautobot.py" > "$ROOT/mock.log" 2>&1 &
  PIDS+=("$!")
  (cd "$API_DIR" && .venv/bin/python -m app migrate) || return 1
  (cd "$API_DIR" && exec .venv/bin/python run.py) > "$ROOT/app.log" 2>&1 &
  PIDS+=("$!")
  # Ready when the first inventory sync has filled the board.
  wait_for "$E2E_BASE_URL/api/v1/alerts" '"name"' 90
}

e2e_token() {
  (cd "$API_DIR" && .venv/bin/python -m app token create "$1" --role "$2" --expires-days 1 | head -n 1)
}

e2e_tokens() {
  # Maintenance needs the operator role (#343), the Admin page and sharing a status
  # page the admin role (#530); each role's workspace (#697).
  E2E_OPERATOR_TOKEN="$(e2e_token e2e operator)" &&
    E2E_ADMIN_TOKEN="$(e2e_token e2e-admin admin)" &&
    E2E_NOC_TOKEN="$(e2e_token e2e-noc noc)" &&
    E2E_CUSTOMER_TOKEN="$(e2e_token e2e-customer customer)" &&
    export E2E_OPERATOR_TOKEN E2E_ADMIN_TOKEN E2E_NOC_TOKEN E2E_CUSTOMER_TOKEN
}

part_e2e() {
  PART=E2E
  trap cleanup EXIT
  cd "$ROOT" || exit 1
  rm -f "$ROOT/app.log" "$ROOT/mock.log"
  need "uv $(uv_version)" ensure_uv
  need "bun $(bun_version)" ensure_bun
  need "Install" e2e_install
  need "Chromium" e2e_chromium
  # `all` has just built them in the web part.
  [ "${CI_PAGES_BUILT:-}" = 1 ] || need "Build the pages" e2e_pages
  need "PostgreSQL" start_postgres
  need "Database" pg_database e2e
  load_local_env
  use_test_services
  need "Start the mock Nautobot and the app" e2e_start
  need "Tokens" e2e_tokens
  # E2E_REQUIRED: a missing E2E_BASE_URL fails instead of skipping every test.
  check "End-to-end tests" env E2E_REQUIRED=1 \
    "$API_DIR/.venv/bin/python" -m pytest e2e/test_e2e.py -v -rs
  report "$PART"
}

part_all() {
  local rc=0
  (part_setup) || exit 1
  (part_api) || rc=1
  if (part_web); then
    export CI_PAGES_BUILT=1
  else
    rc=1
  fi
  (part_e2e) || rc=1
  exit "$rc"
}

case "${1:-}" in
  setup) part_setup ;;
  api) part_api ;;
  web) part_web ;;
  e2e) part_e2e ;;
  all) part_all ;;
  pytest) shift; part_pytest "$@" ;;
  *)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
    ;;
esac
