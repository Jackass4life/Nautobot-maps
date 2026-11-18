"""Check a .env file (#758): ``python -m app.check_env [FILE]`` (default ``.env``; ``-``
reads it from stdin). Never prints a value, only names and line numbers.

- **Missing:** an API setting the file doesn't have (#760: the API has no defaults,
  every setting is in .env).
- **Nothing reads it:** a name no part of the app reads (a typo, a leftover, another
  app's), with the name it may have meant. Removed settings (#739) too.
- **Invalid:** an API setting the API would refuse at startup.
- **Set more than once:** every line but the last is ignored.
- **The default anyway:** another part's setting (compose, the proxy) set to what it is
  without the line, so the line can go.

Exit status 1 when something is missing, unread or invalid.

``--fill`` prints the missing lines instead, with the values the code used before #760
(api/local.env), so an upgraded .env behaves as before::

    python -m app.check_env --fill .env >> .env

In Docker, with the file on the host (the image's entrypoint would migrate and start
the server, so ``--entrypoint python``)::

    docker compose run --rm --no-deps -T --entrypoint python api -m app.check_env - < .env

The file is checked in a child process with an empty environment: importing the
settings reads the environment (#754), and a value broken in it, the reason to run
this, must not stop the check.
"""

import argparse
import io
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

API_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Every setting, with the values the code used before #760 (in the image too).
LOCAL_ENV = API_ROOT / "local.env"
LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# The values of these never reach the output, not even inside an error message.
SECRET = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|PEM|CONTROLLERS|DATABASE_URL")


def lines_by_name(text: str) -> dict[str, list[int]]:
    """Each name the file sets, with the lines that set it."""
    found: dict[str, list[int]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = LINE.match(line)
        if match:
            found.setdefault(match.group(1).upper(), []).append(number)
    return found


def _api_fields() -> dict[str, str]:
    """Each API setting's environment names, mapped to its field."""
    from pydantic import AliasChoices

    from app.core.settings import Settings

    names = {}
    for field, info in Settings.model_fields.items():
        alias = info.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias or field]
        for choice in choices:
            names[str(choice).upper()] = field
    return names


def _local_env() -> dict[str, str]:
    from dotenv import dotenv_values

    return {name: value or "" for name, value in dotenv_values(LOCAL_ENV).items()}


def _isolated() -> Any:
    """Settings from given values only: no environment, no .env."""
    from app.core.settings import Settings

    class Isolated(Settings):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings, **sources):  # type: ignore[no-untyped-def,override]
            return (init_settings,)

    return Isolated


def _problem(error: Exception, name: str) -> str:
    """What is wrong, without the value."""
    from pydantic import ValidationError

    if SECRET.search(name):
        return "not a valid value"
    if isinstance(error, ValidationError):
        return "; ".join(
            item["msg"] for item in error.errors(include_input=False, include_url=False)
        )
    return str(error).splitlines()[0]


def missing(text: str) -> list[str]:
    """The API settings the file doesn't set (#760)."""
    from app.core.settings import REQUIRED

    found = lines_by_name(text)
    return [name for name in REQUIRED if name not in found]


def fill(text: str) -> list[str]:
    """The lines the file is missing, with the values the code used before #760."""
    local = _local_env()
    names = missing(text)
    if not names:
        return []
    return [
        "",
        "# Added by python -m app.check_env --fill: the values the code used before #760.",
        *(f"{name}={_quoted(local[name])}" for name in names),
    ]


def _quoted(value: str) -> str:
    return f"'{value}'" if re.search(r"[\s\"#&<>()$]", value) else value


def check(text: str) -> tuple[list[str], int]:
    """The report on a .env file's *text*, and the exit status."""
    from dotenv import dotenv_values

    from app.core import env_names
    from app.core.settings import REMOVED_FILE_SETTINGS

    found = lines_by_name(text)
    values = {
        name.upper(): value for name, value in dotenv_values(stream=io.StringIO(text)).items()
    }
    fields = _api_fields()
    isolated = _isolated()

    def refusal(field: str, value: str, base: dict[str, str]) -> Exception | None:
        try:
            isolated(**{**base, field: value})
        except Exception as error:  # noqa: BLE001 - any refusal is reported, not raised
            return error
        return None

    # Each setting on its own, the others from api/local.env; one refused alone again
    # with the file's own settings that pass, for those that depend on another
    # (DATABASE_URL empty with NAUTOBOT_MAPS_DATABASE_URL set, AUTH_PROXY_SECRET with
    # AUTH_MODE=header).
    local = _local_env()
    alone = {
        name: refusal(fields[name], values.get(name) or "", local)
        for name in found
        if name in fields
    }
    together = {
        **local,
        **{fields[name]: values.get(name) or "" for name, error in alone.items() if not error},
    }
    unknown, removed, invalid, twice, default = [], [], [], [], []
    for name, numbers in sorted(found.items()):
        where = f"line {numbers[-1]}"
        if len(numbers) > 1:
            twice.append(f"{name}: lines {', '.join(map(str, numbers))} (line {numbers[-1]} wins)")
        if name in env_names.REMOVED:
            instead = REMOVED_FILE_SETTINGS[name]
            removed.append(f"{name} ({where}): removed (#739), set {instead} instead")
            continue
        if name in env_names.NOT_IN_ENV:
            unknown.append(f"{name} ({where}): only the tests, the demo or CI read it")
            continue
        if name not in env_names.KNOWN:
            meant = env_names.suggestion(name)
            unknown.append(f"{name} ({where})" + (f": did you mean {meant}?" if meant else ""))
            continue
        value = values.get(name) or ""
        if name in fields:
            error = alone[name] and refusal(fields[name], value, together)
            if error:
                invalid.append(f"{name} ({where}): {_problem(error, name)}")
        elif value.strip() == env_names.OTHER_PARTS_DEFAULTS.get(name):
            default.append(name)
    absent = missing(text)
    report = [f"{len(found)} settings in the file."]
    sections = [
        ("Missing: the API won't start without them (--fill adds them)", absent),
        ("Nothing reads these", unknown + removed),
        ("Invalid: the API would not start", invalid),
        ("Set more than once: only the last line counts", twice),
        ("The default anyway: the line can go", default),
    ]
    for title, items in sections:
        if items:
            report += ["", f"{title}:"] + [f"  {item}" for item in items]
    if not any(items for _, items in sections):
        report.append("Nothing to report.")
    return report, 1 if absent or unknown or removed or invalid else 0


def _child(fill_in: bool = False) -> int:
    """In the clean child: read the file from stdin, print the report (or the lines)."""
    text = sys.stdin.read()
    if fill_in:
        print("\n".join(fill(text)))
        return 0
    report, status = check(text)
    print("\n".join(report))
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.check_env", description=__doc__.splitlines()[0]
    )
    parser.add_argument("file", nargs="?", default=".env", help=".env file to check; - for stdin")
    parser.add_argument(
        "--fill",
        action="store_true",
        help="print the missing lines, with the values the code used before #760",
    )
    args = parser.parse_args(argv)
    if args.file == "-":
        text = sys.stdin.read()
    else:
        path = pathlib.Path(args.file)
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8")
    # A clean environment, and a directory without a .env, for the settings import: it
    # needs every setting (#760), so api/local.env's.
    with tempfile.TemporaryDirectory() as empty:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; from app import check_env; sys.exit(check_env._child({args.fill!r}))",
            ],
            input=text,
            text=True,
            cwd=empty,
            env={**_local_env(), "PYTHONPATH": str(API_ROOT), "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
