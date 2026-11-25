# shellcheck shell=bash
# Helpers for scripts/ci/test.sh (#765): the platform (GitHub Actions, GitLab CI or a
# local run), the tools, a throwaway PostgreSQL, free ports, log groups, and checks
# that all run and report together.  Sourced, not run.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
API_DIR="${API_DIR:-$ROOT/api}"
WEB_DIR="${WEB_DIR:-$ROOT/web}"
# Where a GitLab shell runner (and a local run) keeps uv and bun between jobs.
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"

if [ -n "${GITHUB_ACTIONS:-}" ]; then
  CI_PLATFORM=github
elif [ -n "${GITLAB_CI:-}" ]; then
  CI_PLATFORM=gitlab
else
  CI_PLATFORM=local
fi
# Names the job's container and databases: unique per job on a shared runner.
JOB_ID="$(printf '%s' "${CI_JOB_ID:-${GITHUB_RUN_ID:-local-$$}-${GITHUB_JOB:-}}" \
  | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '_')"

# -- Output -------------------------------------------------------------------

group_start() {
  case "$CI_PLATFORM" in
    github) echo "::group::$1" ;;
    gitlab) printf '\e[0Ksection_start:%s:%s[collapsed=true]\r\e[0K%s\n' \
              "$(date +%s)" "$(_section_id "$1")" "$1" ;;
    *) printf '\n=== %s\n' "$1" ;;
  esac
}

group_end() {
  case "$CI_PLATFORM" in
    github) echo "::endgroup::" ;;
    gitlab) printf '\e[0Ksection_end:%s:%s\r\e[0K\n' "$(date +%s)" "$(_section_id "$1")" ;;
  esac
}

_section_id() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_'
}

# -- Checks -------------------------------------------------------------------
# `check NAME CMD...` runs CMD in a log group and records a failure without
# stopping: every check of a part runs, then `report` says which failed.

FAILED=()
PASSED=()

check() {
  local name="$1"
  shift
  group_start "$name"
  "$@"
  local rc=$?
  group_end "$name"
  if [ "$rc" -eq 0 ]; then
    PASSED+=("$name")
  else
    FAILED+=("$name")
    [ "$CI_PLATFORM" = github ] && echo "::error title=$name::$name failed (exit $rc)"
    echo "FAILED: $name (exit $rc)" >&2
  fi
  return "$rc"
}

# `report PART`: the part's checks, as a list (and the GitHub job summary); fails
# when any check did.
report() {
  local part="$1" name
  {
    echo "### $part"
    for name in "${PASSED[@]}"; do echo "- ✅ $name"; done
    for name in "${FAILED[@]}"; do echo "- ❌ $name"; done
  } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  [ "${#FAILED[@]}" -eq 0 ]
}

# -- Tools --------------------------------------------------------------------
# The versions live in one place each: uv's in api/pyproject.toml (required-version),
# bun's in web/package.json (packageManager).  Nothing to do when PATH has it already
# (GitHub's setup actions put it there).

uv_version() {
  sed -n 's/^required-version *= *"==\([0-9.]*\)".*/\1/p' "$API_DIR/pyproject.toml"
}

bun_version() {
  sed -n 's/.*"packageManager": *"bun@\([0-9.]*\)".*/\1/p' "$WEB_DIR/package.json"
}

ensure_uv() {
  local want
  want="$(uv_version)"
  if [ "$(uv --version 2>/dev/null | cut -d' ' -f2)" = "$want" ]; then
    return 0
  fi
  echo "Installing uv $want into ~/.local/bin"
  curl -LsSf "https://astral.sh/uv/$want/install.sh" \
    | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
}

ensure_bun() {
  local want
  want="$(bun_version)"
  if [ "$(bun --version 2>/dev/null)" = "$want" ]; then
    return 0
  fi
  echo "Installing bun $want into ~/.bun"
  curl -fsSL https://bun.sh/install | bash -s "bun-v$want"
}

# -- PostgreSQL ---------------------------------------------------------------
# A server for the job: TEST_PG_URL (postgresql://user:password@host:port, no
# database) when set, else a postgres:16-alpine container on a port Docker picks.
# `pg_database NAME` then makes a database on it, its URL in PG_DATABASE_URL; cleanup
# drops them.

PG_URL=""
PG_CONTAINER=""
PG_DATABASES=()
PG_DATABASE_URL=""

start_postgres() {
  if [ -n "${TEST_PG_URL:-}" ]; then
    PG_URL="${TEST_PG_URL%/}"
    return 0
  fi
  PG_CONTAINER="ci-pg-$JOB_ID"
  docker run -d --rm --name "$PG_CONTAINER" \
    -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=postgres \
    -p 127.0.0.1::5432 postgres:16-alpine >/dev/null || return 1
  local attempt
  for attempt in $(seq 1 60); do
    # Over TCP: during its first start the image's init server listens on the socket only.
    if docker exec "$PG_CONTAINER" pg_isready -q -h 127.0.0.1 -U ci -d postgres; then
      PG_URL="postgresql://ci:ci@$(docker port "$PG_CONTAINER" 5432/tcp | head -n 1)"
      return 0
    fi
    sleep 1
  done
  echo "PostgreSQL did not start within 60s (attempt $attempt)" >&2
  return 1
}

_pg_admin() {
  "$API_DIR/.venv/bin/python" "$ROOT/scripts/ci/pg.py" "$@"
}

# Run after `uv sync` (it needs asyncpg); not in $(…): it records the database.
pg_database() {
  local name="ci_${1}_$JOB_ID"
  _pg_admin create "$PG_URL" "$name" || return 1
  PG_DATABASES+=("$name")
  # shellcheck disable=SC2034 # test.sh reads it
  PG_DATABASE_URL="$PG_URL/$name"
}

# -- Processes ----------------------------------------------------------------

PIDS=()

free_port() {
  "$API_DIR/.venv/bin/python" -c \
    'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

# `wait_for URL PATTERN SECONDS`: until URL answers with PATTERN in its body.
wait_for() {
  local url="$1" pattern="$2" seconds="$3" attempt
  for attempt in $(seq 1 "$seconds"); do
    if curl -sf "$url" 2>/dev/null | grep -q "$pattern"; then
      return 0
    fi
    sleep 1
  done
  echo "$url did not answer with $pattern within ${seconds}s (attempt $attempt)" >&2
  return 1
}

# The trap: stop what the part started; on a failure, the end of the logs.
cleanup() {
  local rc=$? pid name
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null
  done
  if [ "$rc" -ne 0 ]; then
    for name in "$ROOT/app.log" "$ROOT/mock.log"; do
      [ -f "$name" ] && { echo "--- last 200 lines of $name"; tail -n 200 "$name"; }
    done
  fi
  if [ -n "$PG_CONTAINER" ]; then
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1
  elif [ "${#PG_DATABASES[@]}" -gt 0 ]; then
    for name in "${PG_DATABASES[@]}"; do
      _pg_admin drop "$PG_URL" "$name" || true
    done
  fi
  return "$rc"
}

# -- Settings -----------------------------------------------------------------

# Every API setting from api/local.env (#760: the API has no defaults), into the
# environment, where it wins over a developer's api/.env; the part then sets its own.
load_local_env() {
  set -a
  # shellcheck source=/dev/null
  . "$API_DIR/local.env"
  set +a
}

# Nothing that only the server has (#774): the tests run before deploy, also when the
# settings come from the deployment's .env.  Redis and AD come up with the server.
use_test_services() {
  export CACHE_TYPE=SimpleCache CACHE_REDIS_URL="" LDAP_URL=""
}

ruff_check() {
  case "$CI_PLATFORM" in
    github) uv run ruff check --output-format=github . ;;
    gitlab)
      # Code Quality JSON for the merge request widget, and readable output for the log.
      uv run ruff check --output-format=gitlab --exit-zero . > ruff-codequality.json
      uv run ruff check . ;;
    *) uv run ruff check . ;;
  esac
}

install_chromium() {
  if [ "$CI_PLATFORM" = github ] || [ "${PLAYWRIGHT_WITH_DEPS:-}" = 1 ]; then
    uv run python -m playwright install --with-deps chromium
  else
    # The system libraries come from the runner's admin, once (CONTRIBUTING.md).
    uv run python -m playwright install chromium
  fi
}
