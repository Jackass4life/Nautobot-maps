"""Command line: ``python -m nautobot_maps migrate`` (#201).

The container runs ``migrate`` once before gunicorn starts, so schema
migrations never run inside worker startup (gunicorn's timeout and the
health check's start period).  ``schema-version`` prints the database's
version and the one this release expects.
"""

import argparse
import logging
import sys

from nautobot_maps import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nautobot_maps")
    parser.add_argument("command", choices=["migrate", "schema-version"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not db.dialect():
        print("No database configured (NAUTOBOT_MAPS_DATABASE_URL); nothing to do.")
        return 0
    if args.command == "migrate":
        db.init_db()
        return 0
    conn = db.get_conn()
    try:
        print(f"database: {db.schema_version(conn)}, this release: {db.SCHEMA_VERSION}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
