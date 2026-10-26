"""Configuration plumbing: settings in .env must reach the app in Docker (#159)."""

import argparse
import os
import pathlib
import re
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Removed settings, only read to warn about them (#153): neither passed by
# docker-compose.yml nor documented as settings.
LEGACY_SETTINGS = {"NAUTOBOT_MAPS_DB"}

# Read by the code but deliberately not passed by docker-compose.yml.
NOT_PASSED_BY_COMPOSE = {
    "FLASK_DEBUG",  # only for `python app.py`; Docker runs gunicorn
    "FLASK_RUN_PORT",  # same
    *LEGACY_SETTINGS,
}


def _settings_read_by_code() -> set[str]:
    names = set()
    for name in ("app.py", "gunicorn_config.py", "nautobot_maps/settings.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        # Any call with a setting's name first: os.getenv(...) and helpers such as
        # _flag("COMPRESS_RESPONSES", True), which the os.getenv-only match missed.
        names |= set(re.findall(r'\w\(\s*"([A-Z][A-Z0-9_]+)"', source))
        names |= set(re.findall(r'os\.environ\[\s*"([A-Z][A-Z0-9_]+)"', source))
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
            "import app, gunicorn_config as g; from nautobot_maps import settings as s; "
            "print(s.CACHE_TTL, g.workers, g.timeout, g.bind, repr(s.NAUTOBOT_API_VERSION))",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "300 4 120 0.0.0.0:5000 ''"


def test_only_the_settings_module_reads_the_environment():
    """Every module reads settings via nautobot_maps.settings, never os.getenv
    (#165), so the documentation check below sees every setting (#519)."""
    files = [REPO_ROOT / "app.py", REPO_ROOT / "asgi.py", *sorted((REPO_ROOT / "nautobot_maps").rglob("*.py"))]
    readers = [
        str(path.relative_to(REPO_ROOT))
        for path in files
        if path.name != "settings.py" and re.search(r"os\.(getenv|environ)", path.read_text(encoding="utf-8"))
    ]
    assert not readers, f"read settings through nautobot_maps.settings: {readers}"


def test_importing_the_modules_does_not_touch_the_database():
    """Only app.py sets up the database at startup, after logging is configured (#165).

    Nothing listens on port 1, so any module connecting at import would fail here.
    """
    modules = sorted(p.stem for p in (REPO_ROOT / "nautobot_maps").glob("*.py") if p.stem != "__init__")
    env = {**os.environ, "NAUTOBOT_MAPS_DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none"}
    completed = subprocess.run(
        [sys.executable, "-c", "; ".join(f"import nautobot_maps.{m}" for m in modules)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert len(modules) >= 7


def test_the_config_folder_is_mounted_read_only():
    """The app's own files (#517): ./config at /config, read-only, and never committed."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "./config:/config:ro" in compose["services"]["nautobot-maps"]["volumes"]
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config/*" in ignored and "!config/README.md" in ignored
    assert (REPO_ROOT / "config" / "README.md").is_file()
    # Nor built into the image: COPY . . would put the secrets in a layer.
    assert "config/" in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_every_setting_is_documented():
    """Every setting the code reads is in docs/configuration.md and .env.example (#519)."""
    configuration = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    settings_read = _settings_read_by_code() - LEGACY_SETTINGS
    missing = sorted(name for name in settings_read if f"`{name}`" not in configuration)
    assert not missing, f"add to docs/configuration.md: {missing}"
    # As a line of its own ("NAME=" or "# NAME="), not inside a longer name.
    missing = sorted(name for name in settings_read if not re.search(rf"^#? ?{name}=", example, re.MULTILINE))
    assert not missing, f"add to .env.example: {missing}"


def test_every_wireless_controller_key_is_documented():
    """Each kind and key a wireless controllers file may have is in the key
    table of docs/sources.md (#519): a row's first cell, or a column title."""
    from nautobot_maps import settings

    sources = (REPO_ROOT / "docs" / "sources.md").read_text(encoding="utf-8")
    table = sources.split("**Every key in the file**", 1)[1].split("\n\n", 2)[1]
    first_cells = " ".join(row.split("|")[1] for row in table.splitlines() if row.startswith("|"))
    header = table.splitlines()[0]
    keys = {key for required, optional in settings.WIRELESS_CONTROLLER_KINDS.values() for key in required | optional}
    missing = sorted(key for key in keys if f"`{key}`" not in first_cells)
    missing += sorted(kind for kind in settings.WIRELESS_CONTROLLER_KINDS if f"`{kind}`" not in header)
    assert not missing, f"add to the key table in docs/sources.md: {missing}"


def _commands(parser) -> list[str]:
    """Every command and subcommand, as typed: "migrate", "token create"."""
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                below = _commands(sub)
                found += [f"{name} {rest}" for rest in below] if below else [name]
    return found


def test_every_command_is_documented():
    """Each ``python -m nautobot_maps`` command, as typed, is in the README or a guide (#519)."""
    from nautobot_maps import __main__ as cli

    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
    )
    commands = _commands(cli.build_parser())
    assert "token create" in commands
    missing = sorted(command for command in commands if f"nautobot_maps {command}" not in docs)
    assert not missing, f"document these commands (python -m nautobot_maps ...): {missing}"
