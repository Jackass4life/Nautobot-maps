"""Configuration plumbing: settings in .env must reach the app in Docker (#159)."""

import os
import pathlib
import re
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Read by the code but deliberately not passed by docker-compose.yml.
NOT_PASSED_BY_COMPOSE = {
    "FLASK_DEBUG",  # only for `python app.py`; Docker runs gunicorn
    "FLASK_RUN_PORT",  # same
    "NAUTOBOT_MAPS_DB",  # removed SQLite setting, only read to warn (#153)
}


def _settings_read_by_code() -> set[str]:
    names = set()
    for name in ("app.py", "gunicorn_config.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        names |= set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"', source))
    return names


def test_docker_compose_passes_every_setting():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    passed = {entry.split("=", 1)[0] for entry in compose["services"]["nautobot-maps"]["environment"]}
    missing = _settings_read_by_code() - passed - NOT_PASSED_BY_COMPOSE
    assert not missing, f"add to docker-compose.yml (or NOT_PASSED_BY_COMPOSE): {sorted(missing)}"


def test_empty_settings_mean_defaults():
    """docker-compose passes unset variables as ""; startup must not crash on them."""
    env = {**os.environ}
    for name in ("CACHE_TTL", "GUNICORN_WORKERS", "GUNICORN_TIMEOUT", "GUNICORN_BIND", "NAUTOBOT_API_VERSION"):
        env[name] = ""
    env["AUTH_MODE"] = "disabled"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app, gunicorn_config as g; "
            "print(app.CACHE_TTL, g.workers, g.timeout, g.bind, repr(app.NAUTOBOT_API_VERSION))",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "300 4 120 0.0.0.0:5000 ''"
