"""Write the API spec the frontend's types are made from (#463):
``python frontend/scripts/openapi.py`` from the repository root, then
``npm run api-types`` in frontend/.  tests/test_frontend.py fails while
frontend/openapi.json is not the app's current spec."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import asgi  # noqa: E402


def spec_text() -> str:
    return json.dumps(asgi.api.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    (ROOT / "frontend" / "openapi.json").write_text(spec_text())
