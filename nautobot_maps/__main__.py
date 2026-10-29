"""Command line: ``python -m nautobot_maps migrate`` (#201).

The container runs ``migrate`` once before gunicorn starts, so schema
migrations never run inside worker startup (gunicorn's timeout and the
health check's start period).  ``schema-version`` prints the database's
version and the one this release expects.

``contract-docs`` prints docs/data-contract.md from the data contract (#309).

``token create NAME --role operator [--expires-days N]``, ``token list
[--all] [--tenant TENANT]``, ``token revoke ID`` and ``token purge
[--older-than DAYS] [--dry-run]`` manage API tokens (#297, #528); ``create``
is how the first admin token is made (``docker compose exec app python -m
nautobot_maps ...``).

``status-link TENANT`` prints the tenant's status page link, ``/status/<id>``
(#500); ``--new`` replaces it, and the old link stops working.

``wireless-check`` reads every wireless controller in WIRELESS_CONTROLLERS_FILE
now (#508) and says, for each, whether it answers, how many access points it
lists and how many match a Nautobot device; exit status 1 if one fails.
"""

import argparse
import logging
import sys

from nautobot_maps import contract, db, statuslinks, tokens, wireless


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nautobot_maps")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")
    commands.add_parser("schema-version", help="print the database's schema version")
    commands.add_parser("contract-docs", help="print docs/data-contract.md from the data contract")
    token = commands.add_parser("token", help="manage API tokens")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    create = token_commands.add_parser("create", help="create a token and print it (shown once)")
    create.add_argument("name")
    create.add_argument("--role", required=True, choices=(*tokens.ROLES, tokens.STATUS_ROLE))
    create.add_argument("--tenant", default=None, help="with --role status: the tenant whose status page it opens")
    create.add_argument("--expires-days", type=int, default=None)
    listing = token_commands.add_parser("list", help="list the active tokens (never the secrets)")
    listing.add_argument("--all", action="store_true", help="revoked and expired ones too")
    listing.add_argument("--tenant", default=None, help="only this tenant's status tokens")
    revoke = token_commands.add_parser("revoke", help="revoke a token by its ID")
    revoke.add_argument("id", type=int)
    purge = token_commands.add_parser("purge", help="delete revoked and expired tokens for good (#528)")
    purge.add_argument(
        "--older-than", type=int, default=None, metavar="DAYS", help="only those revoked or expired over DAYS ago"
    )
    purge.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
    link = commands.add_parser("status-link", help="print a tenant's status page link (#500)")
    link.add_argument("tenant")
    link.add_argument("--new", action="store_true", help="replace it: the old link stops working")
    commands.add_parser("wireless-check", help="read each wireless controller now and say what matched (#508)")
    return parser


def wireless_check_command() -> int:
    if not wireless.configured():
        print("No wireless controllers (WIRELESS_CONTROLLERS_FILE); nothing to check.")
        return 0
    failed = False
    for result in wireless.check():
        if "duplicates" in result:
            print(
                f"{result['duplicate_count']} access point(s) listed by more than one controller (the newest answer wins):"
            )
            for serial, where in result["duplicates"].items():
                print(f"  {serial}: {', '.join(where)}")
            continue
        if "error" in result:
            failed = True
            print(f"{result['controller']} ({result['url']}): can't read it: {result['error']}")
            continue
        print(
            f"{result['controller']} ({result['url']}): {result['access_points']} access points, "
            f"{result['down']} down, {result['matched']} match a Nautobot device"
        )
        if result["unmatched_examples"]:
            print(f"  not in Nautobot (serial or name), e.g.: {', '.join(result['unmatched_examples'])}")
    return 1 if failed else 0


def status_link_command(conn, args) -> int:
    if not args.tenant.strip() or not statuslinks.known(conn, args.tenant):
        # A typo must not make a link, or leave a leaked one working.
        print(f"error: {statuslinks.unknown_tenant(args.tenant)}", file=sys.stderr)
        return 1
    if args.new:
        link_id = statuslinks.replace(conn, args.tenant)
    else:
        link_id = statuslinks.ensure(conn, [args.tenant])[args.tenant]
    print(f"/status/{link_id}")
    print(
        "The status page link; with a status token, share it as /status/<id>#key=<token>."
        + (" The old link no longer works." if args.new else ""),
        file=sys.stderr,
    )
    return 0


def print_token(token: dict) -> None:
    tenant = f"  (tenant {token['tenant']})" if token.get("tenant") else ""
    ended = f"  revoked {token['revoked_at']}" if token.get("revoked_at") else ""
    print(
        f"{token['id']:>4}  {token['prefix']}…  {token['role']:<8}  {token['state']:<7}  "
        f"expires {token['expires_at'] or 'never'}  last used {token['last_used_at'] or 'never'}{ended}  "
        f"{token['name']}{tenant}"
    )


def token_command(conn, args) -> int:
    if args.token_command == "create":
        body = {"name": args.name, "role": args.role, "expires_in_days": args.expires_days}
        if args.tenant is not None:
            body["tenant"] = args.tenant
        try:
            token = tokens.create(conn, body, "cli")
        except tokens.TokenError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        print(token["token"])
        print(
            f"Token {token['name']!r} (id {token['id']}, role {token['role']}, "
            f"expires {token['expires_at'] or 'never'}). Shown only now: store it.",
            file=sys.stderr,
        )
        if token.get("tenant"):
            # The link to send the customer (#500); the token is made either way.
            try:
                link_id = statuslinks.ensure(conn, [token["tenant"]])[token["tenant"]]
            except Exception as exc:
                print(f"warning: no status link ({exc}); try: status-link {token['tenant']!r}", file=sys.stderr)
            else:
                print(f"Share: /status/{link_id}#key={token['token']}", file=sys.stderr)
        return 0
    if args.token_command == "list":
        rows = tokens.list_tokens(conn, tenant=args.tenant)
        shown = rows if args.all else [token for token in rows if token["state"] == "active"]
        if not shown:
            print("No active tokens." if rows else "No tokens.")
        for token in shown:
            print_token(token)
        if len(shown) < len(rows):
            print(f"+{len(rows) - len(shown)} revoked or expired (--all to show)")
        return 0
    if args.token_command == "purge":
        try:
            purged = tokens.purge(conn, "cli", older_than_days=args.older_than, dry_run=args.dry_run)
        except tokens.TokenError as exc:
            print(f"error: {exc.message.replace('older_than_days', '--older-than')}", file=sys.stderr)
            return 1
        for token in purged:
            print_token(token)
        if args.dry_run:
            print(f"{len(purged)} token(s) would be purged (--dry-run: nothing deleted).")
        else:
            print(f"{len(purged)} token(s) purged; their names are free again.")
        return 0
    token = tokens.revoke(conn, args.id, "cli")
    if token is None:
        print(f"error: no token with id {args.id}", file=sys.stderr)
        return 1
    print(f"Token {token['name']!r} is {token['state']}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "contract-docs":
        sys.stdout.write(contract.render_markdown())
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "wireless-check":
        # Without a database it still says whether each controller answers.
        return wireless_check_command()

    if not db.dialect():
        print("No database configured (NAUTOBOT_MAPS_DATABASE_URL); nothing to do.")
        return 0 if args.command not in ("token", "status-link") else 1
    if args.command == "migrate":
        db.init_db()
        return 0
    conn = db.get_conn()
    try:
        if args.command == "token":
            return token_command(conn, args)
        if args.command == "status-link":
            return status_link_command(conn, args)
        print(f"database: {db.schema_version(conn)}, this release: {db.SCHEMA_VERSION}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
