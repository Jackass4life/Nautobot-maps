"""Write the OpenAPI spec to `openapi.json`: `uv run python -m app.export_openapi`."""

import json
import sys
from pathlib import Path

from app.main import app

OUTPUT = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT
    target.write_text(render())
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
