"""A page for some sites only, on the server (#368, #369).

The twin of ``static/js/lib/scope.js``: ``?tenant=`` (repeatable, any of
them) and ``?q=`` (repeatable, every one of them), matched on the same
fields, case ignored.  Tests run the same cases through both.  Needed where
the server must filter itself: the coordinate list's CSV (#368) and the
activity feed, whose limit must count the matching events (#369).
"""

from nautobot_maps import status

# The query parameters a scoped endpoint takes (the API page lists them).
QUERY_PARAMS = ("tenant", "q")


def read_scope(args) -> tuple[list[str], list[str]] | None:
    """(tenants, texts), lower case, from request args; None without either."""
    tenants = [name.strip().lower() for name in args.getlist("tenant") if name.strip()]
    texts = [text.strip().lower() for text in args.getlist("q") if text.strip()]
    return (tenants, texts) if tenants or texts else None


def search_text(row: dict) -> str:
    """Name, path, address and country, as the board's filter box searches."""
    address = row.get("physical_address") or row.get("facility") or ""
    parts = [row.get("name"), row.get("ancestor_path") or row.get("parent"), address, row.get("country")]
    return " ".join(part or "" for part in parts).lower()


def in_scope(row: dict, scope: tuple[list[str], list[str]] | None) -> bool:
    if scope is None:
        return True
    tenants, texts = scope
    if tenants and not any(name.lower() in tenants for name in status.site_tenants(row)):
        return False
    text = search_text(row) if texts else ""
    return all(needle in text for needle in texts)
