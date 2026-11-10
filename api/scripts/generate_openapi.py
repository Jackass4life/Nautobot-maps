"""Write the OpenAPI spec to `web/api/openapi.json`, which the pages' typed client
is made from: `uv run python scripts/generate_openapi.py` from api/ (or give
another path), then `bun run api:generate` in web/."""

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
# Run as a script, Python puts scripts/ first on the path, not api/.
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api import app  # noqa: E402

OUTPUT = API_ROOT.parent / "web" / "api" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT
    target.write_text(render())
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
