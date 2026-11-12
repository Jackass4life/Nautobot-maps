"""The command line, ``python -m app`` (#611): the current app's CLI tests
(tests/test_tokens.py, tests/test_config.py), on the new stack."""

import argparse
import logging
import pathlib

import pytest

from app import __main__ as cli
from app.db import connection as db
from app.services import token_service as tokens
from app.utils import timeutil
from tests import db as adb

NOW = "2026-10-09T12:00:00Z"
API_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


def make(name="script", role="operator", **extra) -> dict:
    return adb.run(tokens.create, {"name": name, "role": role, **extra}, "admin-user")


def verify(token: str) -> dict | None:
    return adb.run(tokens.verify, token)


def raw_rows() -> list[dict]:
    conn = adb.connect()
    try:
        return [
            db.row_to_dict(row)
            for row in conn.execute("SELECT * FROM api_tokens ORDER BY id").fetchall()
        ]
    finally:
        conn.close()


class TestTokens:
    def test_create_list_revoke(self, pg_database, clock, capsys):
        assert (
            cli.main(["token", "create", "librenms", "--role", "operator", "--expires-days", "30"])
            == 0
        )
        out, err = capsys.readouterr()
        secret = out.strip()
        assert secret.startswith("nmt_") and "Shown only now" in err and secret not in err
        assert verify(secret) == {"name": "librenms", "role": "operator", "tenant": ""}
        assert raw_rows()[0]["created_by"] == "cli"
        assert cli.main(["token", "create", "librenms", "--role", "viewer"]) == 1
        assert "already exists" in capsys.readouterr().err
        assert cli.main(["token", "list"]) == 0
        listing = capsys.readouterr().out
        assert "librenms" in listing and secret[:12] in listing and secret not in listing
        token_id = raw_rows()[0]["id"]
        assert cli.main(["token", "revoke", str(token_id)]) == 0
        assert "is revoked" in capsys.readouterr().out
        assert cli.main(["token", "revoke", "999"]) == 1

    @pytest.fixture
    def mixed(self, pg_database, clock):
        """An active token, one revoked 100 days ago, one expired 10 days ago, one revoked now."""
        clock["now"] = "2026-06-01T12:00:00Z"
        old = make("old-revoked")
        expiring = make("expired", expires_in_days=120)  # expires 2026-09-29
        adb.run(tokens.revoke, old["id"], "admin-user")
        clock["now"] = NOW
        fresh = make("fresh-revoked")
        adb.run(tokens.revoke, fresh["id"], "admin-user")
        active = make("active")
        return {"active": active, "old": old, "expired": expiring, "fresh": fresh}

    def test_the_list_shows_the_active_ones(self, mixed, capsys):
        assert cli.main(["token", "list"]) == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2 and lines[0].endswith("  active")
        assert lines[1] == "+3 revoked or expired (--all to show)"
        assert cli.main(["token", "list", "--all"]) == 0
        out = capsys.readouterr().out
        assert all(name in out for name in ("old-revoked", "expired", "fresh-revoked"))
        assert "+" not in out

    def test_purge_never_an_active_one(self, mixed, capsys, caplog):
        caplog.set_level(logging.INFO, logger="app.services.token_service")
        assert cli.main(["token", "purge", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "3 token(s) would be purged" in out and len(raw_rows()) == 4
        assert cli.main(["token", "purge", "--older-than", "30"]) == 0
        assert "1 token(s) purged" in capsys.readouterr().out
        assert sorted(row["name"] for row in raw_rows()) == ["active", "expired", "fresh-revoked"]
        assert "API token 'old-revoked' purged by cli" in caplog.text
        assert cli.main(["token", "purge"]) == 0
        assert [row["name"] for row in raw_rows()] == ["active"]
        assert verify(mixed["active"]["token"])
        assert cli.main(["token", "purge", "--older-than", "-1"]) == 1
        assert "--older-than must be a whole number" in capsys.readouterr().err


def test_schema_version(pg_database, capsys):
    """The database's revision against this release's head (the tests' database is at head)."""
    assert cli.main(["schema-version"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    current, head = first.removeprefix("database: ").split(", this release: ")
    assert current == head and head != "none"


def test_contract_docs_is_the_checked_in_file(capsys):
    assert cli.main(["contract-docs"]) == 0
    assert capsys.readouterr().out == (API_ROOT.parent / "docs" / "data-contract.md").read_text()


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
    """Each ``python -m app`` command, as typed, is in api/README.md (#519)."""
    docs = (API_ROOT / "README.md").read_text(encoding="utf-8")
    commands = _commands(cli.build_parser())
    assert "token create" in commands
    missing = sorted(command for command in commands if f"python -m app {command}" not in docs)
    assert not missing, f"document these commands in api/README.md: {missing}"
