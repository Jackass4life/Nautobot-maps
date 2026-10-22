"""Every URL, method and role the app serves (#424), frozen: moving routes
between modules must not change any of them.  After a real change of routes,
regenerate the file with `python -m tests.test_route_table`."""

import json
import pathlib

SNAPSHOT = pathlib.Path(__file__).with_name("route_table.json")
IGNORED = {"HEAD", "OPTIONS"}


def route_table(app) -> list[dict]:
    """Flask's routes and FastAPI's (#436), in Flask's notation."""
    from nautobot_maps import apidocs

    rows = [
        {
            "path": route["path"],
            "methods": route["methods"],
            "required_role": route["required_role"],
            "view": route["view"].__name__,
        }
        for route in apidocs.routes(app)
    ]
    return sorted(rows, key=lambda row: (row["path"], row["methods"]))


def test_no_route_changed():
    from app import app

    assert route_table(app) == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    from app import app

    SNAPSHOT.write_text(json.dumps(route_table(app), indent=1) + "\n", encoding="utf-8")
