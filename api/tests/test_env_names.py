"""The names anything reads (app/core/env_names.py), the startup warning about the
rest, and ``python -m app.check_env`` (#758)."""

import re
import subprocess
import sys

from app import check_env, startup
from app.core import env_names
from app.core import settings as config
from app.core.settings import SETTING_NAMES
from tests.test_settings import API_ROOT, COMPOSE_FILES, REPO_ROOT, config_rows


def test_the_known_names_are_the_documented_ones():
    """docs/configuration.md's tables are the list: a name added to one goes in the other."""
    rows = config_rows()
    documented = {name for names, _ in rows for name in names}
    assert documented == env_names.KNOWN - env_names.REMOVED - {"NAUTOBOT_MAPS_DB"}
    # Rows read by files (tests, the demo, CI) are "Not in .env"; the rest are .env's.
    scripts = {name for names, cell in rows for name in names if "/" in cell.split(" (")[0]}
    in_env = {name for names, cell in rows for name in names if "/" not in cell.split(" (")[0]}
    assert env_names.OTHER_PARTS == in_env - set(SETTING_NAMES)
    assert env_names.NOT_IN_ENV == scripts - in_env - set(SETTING_NAMES)


def test_the_other_parts_defaults_are_theirs():
    """compose's ${NAME:-default}, the entrypoint's and the health check's."""
    sources = "\n".join(
        (REPO_ROOT / name).read_text("utf-8")
        for name in (*COMPOSE_FILES, "api/docker-entrypoint.sh")
    )
    for name, default in env_names.OTHER_PARTS_DEFAULTS.items():
        if name == "HEALTHCHECK_PORT":
            dockerfile = (API_ROOT / "Dockerfile").read_text("utf-8")
            assert f"os.environ.get('HEALTHCHECK_PORT', '{default}')" in dockerfile
            continue
        assert f"${{{name}:-{default}}}" in sources, name


def test_names_that_look_like_ours_are_unknown_with_a_suggestion():
    names = [
        "AUTH_MOD",
        "DB_USER",
        "PATH",
        "HOSTNAME",
        "PYTHON_VERSION",
        "POSTGRES_USER",
        "AUTH_MODE",
    ]
    assert env_names.unknown(names) == [("AUTH_MOD", "AUTH_MODE"), ("DB_USER", None)]


def test_startup_warns_about_names_nothing_reads(monkeypatch, caplog):
    monkeypatch.setattr(config, "environment_names", lambda: ["NOTIFY_EMAILS_TO", "HOME"])
    startup.log_unknown_settings()
    assert [record.getMessage() for record in caplog.records] == [
        "Unknown setting NOTIFY_EMAILS_TO (did you mean NOTIFY_EMAIL_TO?): ignored"
    ]


SAMPLE = """\
# a comment
NAUTOBOT_URL=https://nautobot.example.com
API_PORT=5000
AUTH_MOD=header
CRITICALITY_RULES_FILE=/config/rules.json
FLAP_CHANGES=4
AUTH_MODE=header
AUTH_MODE=disabled only: allow changing criticality overrides
AUTH_MODE=header
PROXY_HTTPS_PORT=443
LOG_LEVEL=LOUD
SMTP_PASSWORD=hunter2-secret
WIRELESS_CONTROLLERS='{"aruba_central": [{"password": "hunter3-secret"'
"""


def test_check_env_reports_without_values():
    report, status = check_env.check(SAMPLE)
    text = "\n".join(report)
    assert status == 1
    assert "API_PORT (line 3)" in text
    assert "AUTH_MOD (line 4): did you mean AUTH_MODE?" in text
    assert "CRITICALITY_RULES_FILE (line 5): removed (#739), set CRITICALITY_RULES" in text
    assert "LOG_LEVEL (line 11): Invalid LOG_LEVEL" in text
    assert "WIRELESS_CONTROLLERS (line 13): not a valid value" in text
    assert "AUTH_MODE: lines 7, 8, 9 (line 9 wins)" in text
    defaults = text.split("The default anyway")[1]
    assert "FLAP_CHANGES" in defaults and "PROXY_HTTPS_PORT" in defaults
    assert "AUTH_MODE" not in defaults and "NAUTOBOT_URL" not in defaults
    assert "hunter" not in text


def test_a_clean_file_is_nothing_to_report():
    report, status = check_env.check("NAUTOBOT_URL=https://n.example.com\nNAUTOBOT_TOKEN=t\n")
    assert status == 0 and report[-1] == "Nothing to report."


def test_check_env_runs_with_a_broken_environment(tmp_path):
    """The command itself, in a child process: the environment it runs in may hold the
    broken value it is meant to find (#754 reads it at import)."""
    path = tmp_path / ".env"
    path.write_text("LOG_LEVEL=LOUD\nAPI_PORT=1\n", encoding="utf-8")
    env = {"LOG_LEVEL": "LOUD", "PATH": "/usr/bin:/bin"}
    for args, stdin in (([str(path)], None), (["-"], path.read_text(encoding="utf-8"))):
        result = subprocess.run(
            [sys.executable, "-m", "app.check_env", *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=API_ROOT,
            env=env,
            check=False,
        )
        assert result.returncode == 1, result.stderr
        assert re.search(r"API_PORT \(line 2\)", result.stdout)
        assert "Invalid LOG_LEVEL" in result.stdout
