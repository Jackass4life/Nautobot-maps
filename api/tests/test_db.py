"""app/db/connection.py (#600, #664): the ported code's row and key helpers."""

from datetime import UTC, datetime

from app.db import connection as db


def test_rows_as_the_current_app_answers_them():
    assert db.row_to_dict({"at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "n": 1}) == {
        "at": "2026-01-02T03:04:05Z",
        "n": 1,
    }
    assert db.row_to_dict(None) == {}
    assert db.placeholders(3) == "%s,%s,%s"
    assert len(db.build_alert_key("site", "dev")) == 64


def test_the_lock_key_is_the_current_apps():
    """nautobot_maps.db.advisory_lock_key's: the two apps' locks are the same locks."""
    assert db.advisory_lock_key("background_scheduler") == -7628381906339041118
