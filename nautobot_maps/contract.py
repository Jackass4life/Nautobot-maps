"""The data contract with Nautobot and LibreNMS (#309).

What the app reads from each upstream API, which fields it uses, their
types, and what each becomes in the app's own tables.  Every sync checks
the records it fetched against this (``check``): a field that is missing or
has another type is logged once per sync, stored in ``contract_checks``,
counted in ``/metrics`` and shown by ``GET /api/contract``.  A mismatch is a
warning, not an error: the sync goes on with what it can use.

``docs/data-contract.md`` is generated from this module
(``python -m nautobot_maps contract-docs``); a test keeps the two in step.
"""

import logging
from dataclasses import dataclass, field

from nautobot_maps import db

logger = logging.getLogger(__name__)

NAUTOBOT = "nautobot"
LIBRENMS = "librenms"
# Brief nested objects (Nautobot 3.x at depth 0) have only id and url; the
# name comes from a lookup endpoint then.
NESTED = ("object", "null")


@dataclass(frozen=True)
class Field:
    path: str  # "status" or "status.id": a nested field is checked when its parent is an object
    types: tuple[str, ...]  # JSON types: string, number, integer, boolean, object, array, null
    required: bool = False  # missing (not just null) is a mismatch
    becomes: str = ""  # our field ("" when only used to look something up)
    rule: str = ""  # how it is rewritten


@dataclass(frozen=True)
class Endpoint:
    source: str
    path: str
    used_for: str
    fields: tuple[Field, ...]
    request: str = ""
    stored_in: str = ""
    checked: bool = True  # False: documented only (inbound requests)
    notes: tuple[str, ...] = field(default_factory=tuple)


def _names(parent: str, *keys: str) -> tuple[Field, ...]:
    """The keys a nested object's name is read from, in that order (a brief
    object has none of them: the name then comes from a lookup by id)."""
    return tuple(Field(f"{parent}.{key}", ("string", "null")) for key in keys)


def _lookup(path: str, used_for: str) -> Endpoint:
    return Endpoint(
        NAUTOBOT,
        path,
        used_for,
        (
            Field("id", ("string",), required=True, rule="key of the lookup"),
            Field("name", ("string", "null"), rule="the first of name, display, label or slug that is set"),
            Field("display", ("string", "null")),
            Field("label", ("string", "null")),
            Field("slug", ("string", "null")),
        ),
        request="all pages",
    )


LIBRENMS_DEVICE_FIELDS = (
    Field("device_id", ("integer",), required=True, becomes="device_id"),
    Field(
        "hostname",
        ("string",),
        required=True,
        becomes="hostname",
        rule="matched to the Nautobot device name (short name, case-insensitive) or, when an IP, to its primary IP",
    ),
    Field(
        "status",
        ("integer", "boolean"),
        required=True,
        becomes="status",
        rule="1/true = up, 0/false = down; down marks an active Nautobot device offline",
    ),
    Field("status_reason", ("string", "null"), becomes="status_reason"),
    Field(
        "overwrite_ip",
        ("string", "null"),
        becomes="ip",
        rule="the address LibreNMS polls: overwrite_ip, else ip; only IP literals",
    ),
    Field("ip", ("string", "null"), becomes="ip"),
)

CONTRACT: tuple[Endpoint, ...] = (
    Endpoint(
        NAUTOBOT,
        "dcim/locations/",
        "Map markers, the alert board's rows and their hierarchy",
        (
            Field("id", ("string",), required=True, becomes="id"),
            Field("name", ("string",), required=True, becomes="name", rule='"Unknown" when empty'),
            Field("display", ("string", "null"), rule="a parent's name when name is empty"),
            Field("slug", ("string", "null"), becomes="slug"),
            Field(
                "status",
                NESTED + ("string",),
                required=True,
                becomes="status",
                rule="label, name or display; else extras/statuses by id",
            ),
            Field("status.id", ("string",)),
            *_names("status", "label", "name", "display"),
            Field(
                "location_type",
                NESTED + ("string",),
                required=True,
                becomes="location_type",
                rule="name or display; else dcim/location-types by id",
            ),
            Field("location_type.id", ("string",)),
            *_names("location_type", "name", "display"),
            Field(
                "parent",
                NESTED,
                required=True,
                becomes="parent, parent_id",
                rule="parent_id = parent.id; parent = its name, or the name of that location",
            ),
            Field("parent.id", ("string",)),
            *_names("parent", "name", "display"),
            Field(
                "tenant",
                NESTED,
                required=True,
                becomes="tenant, tenant_id, tenant_group",
                rule="name or display; else tenancy/tenants by id; tenant_group from the tenant",
            ),
            Field("tenant.id", ("string",)),
            *_names("tenant", "name", "display"),
            Field(
                "latitude",
                ("number", "string", "null"),
                required=True,
                becomes="latitude",
                rule="float; a location without both coordinates has no map marker",
            ),
            Field("longitude", ("number", "string", "null"), required=True, becomes="longitude", rule="float"),
            Field("physical_address", ("string", "null"), becomes="physical_address", rule="trimmed"),
            Field(
                "country",
                ("object", "string", "null"),
                becomes="country",
                rule="name, display or label; else country_name; else the last part of physical_address",
            ),
            *_names("country", "name", "display", "label"),
            Field("country_name", ("string", "null"), becomes="country"),
            Field("description", ("string", "null"), becomes="description"),
            Field("facility", ("string", "null"), becomes="facility"),
            Field("time_zone", ("string", "null"), becomes="time_zone"),
            Field("asn", ("integer", "null"), becomes="asn"),
            Field("tags", ("array", "null"), becomes="tags", rule="each tag's name or display; else extras/tags by id"),
            Field("url", ("string", "null"), becomes="url"),
            Field(
                "last_updated",
                ("string",),
                required=True,
                becomes="last_updated",
                rule="the incremental sync's watermark (last_updated__gte)",
            ),
        ),
        request="all pages; incremental with last_updated__gte",
        stored_in="nautobot_location_cache",
    ),
    Endpoint(
        NAUTOBOT,
        "dcim/devices/",
        "The devices a site's alert level is computed from",
        (
            Field("id", ("string",), required=True, becomes="id"),
            Field("name", ("string", "null"), required=True, becomes="name", rule='"Unknown" when empty'),
            Field(
                "status",
                NESTED + ("string",),
                required=True,
                becomes="status",
                rule="label, name or display; else extras/statuses by id; decides up or down",
            ),
            Field("status.id", ("string",)),
            *_names("status", "label", "name", "display"),
            Field(
                "role",
                NESTED + ("string",),
                required=True,
                becomes="role",
                rule="name or display; else extras/roles by id; decides criticality",
            ),
            Field("role.id", ("string",)),
            *_names("role", "name", "display"),
            Field("location", NESTED, required=True, becomes="location_id", rule="location.id"),
            Field("location.id", ("string",), required=True),
            Field(
                "primary_ip4",
                ("object", "string", "null"),
                required=True,
                becomes="primary_ip",
                rule="host, address, display or name of primary_ip4, else primary_ip6, else primary_ip; a device without one is not monitored",
            ),
            *_names("primary_ip4", "host", "address", "display", "name"),
            Field("primary_ip6", ("object", "string", "null")),
            *_names("primary_ip6", "host", "address", "display", "name"),
            Field("primary_ip", ("object", "string", "null"), rule="older Nautobot"),
            *_names("primary_ip", "host", "address", "display", "name"),
            Field(
                "device_type",
                NESTED,
                required=True,
                becomes="device_type, manufacturer",
                rule="model or display, manufacturer name; else dcim/device-types and dcim/manufacturers by id",
            ),
            Field("device_type.id", ("string",)),
            *_names("device_type", "model", "display"),
            Field("device_type.manufacturer", NESTED),
            Field("device_type.manufacturer.id", ("string",)),
            *_names("device_type.manufacturer", "name", "display"),
            Field("platform", NESTED + ("string",), becomes="platform", rule="name or display"),
            *_names("platform", "name", "display"),
            Field("tenant", NESTED, becomes="tenant", rule="name or display; else tenancy/tenants by id"),
            Field("tenant.id", ("string",)),
            *_names("tenant", "name", "display"),
            Field("serial", ("string", "null"), becomes="serial"),
            Field(
                "last_updated",
                ("string",),
                required=True,
                becomes="last_updated",
                rule="the incremental sync's watermark",
            ),
        ),
        request="all pages, depth=1; incremental with last_updated__gte",
        stored_in="nautobot_device_cache",
    ),
    Endpoint(
        NAUTOBOT,
        "tenancy/tenants/",
        "Tenant names, descriptions (tooltip) and tenant groups",
        (
            Field("id", ("string",), required=True, becomes="tenant_id"),
            Field(
                "name", ("string",), required=True, becomes="name", rule="name, else display (label, slug in lookups)"
            ),
            Field("display", ("string", "null")),
            Field("label", ("string", "null")),
            Field("slug", ("string", "null")),
            Field("description", ("string", "null"), becomes="description", rule="trimmed"),
            Field(
                "tenant_group",
                NESTED,
                becomes="tenant_group (of a location)",
                rule="name or display; else tenancy/tenant-groups by id",
            ),
            Field("tenant_group.id", ("string",)),
            *_names("tenant_group", "name", "display"),
        ),
        request="all pages",
        stored_in="nautobot_tenant_cache",
    ),
    Endpoint(
        NAUTOBOT,
        "extras/relationships/",
        "Which Relationships link locations and tenants",
        (
            Field("id", ("string",), required=True, rule="to read its associations"),
            Field(
                "source_type",
                ("string",),
                required=True,
                rule='kept when source and destination are "dcim.location" and "tenancy.tenant"',
            ),
            Field("destination_type", ("string",), required=True),
            Field(
                "key",
                ("string", "null"),
                becomes="relationship",
                rule="key, else slug; matched against SITE_TENANT_RELATIONSHIPS",
            ),
            Field("slug", ("string", "null")),
            Field("label", ("string", "null"), becomes="relationship", rule="label, name or display, else key"),
            Field("name", ("string", "null")),
            Field("display", ("string", "null")),
        ),
        request="all pages",
    ),
    Endpoint(
        NAUTOBOT,
        "extras/relationship-associations/",
        "The tenants linked to each location (#238)",
        (
            Field(
                "source_id",
                ("string",),
                required=True,
                becomes="location_id or tenant_id",
                rule="by the relationship's direction",
            ),
            Field("destination_id", ("string",), required=True, becomes="tenant_id or location_id"),
        ),
        request="all pages, relationship=<id>",
        stored_in="nautobot_location_tenant_cache",
    ),
    _lookup("extras/statuses/", "Status names when a nested status is brief"),
    _lookup("extras/roles/", "Device role names when a nested role is brief"),
    _lookup("dcim/location-types/", "Location type names when nested ones are brief"),
    _lookup("extras/tags/", "Tag names when nested tags are brief"),
    _lookup("dcim/manufacturers/", "Manufacturer names when nested ones are brief"),
    _lookup("tenancy/tenant-groups/", "Tenant group names when nested ones are brief"),
    Endpoint(
        NAUTOBOT,
        "dcim/device-types/",
        "Model and manufacturer when a device's device_type is brief",
        (
            Field("id", ("string",), required=True, rule="key of the lookup"),
            Field("model", ("string", "null"), becomes="device_type", rule="model, else display"),
            Field("display", ("string", "null")),
            Field(
                "manufacturer", NESTED, becomes="manufacturer", rule="name or display; else dcim/manufacturers by id"
            ),
            Field("manufacturer.id", ("string",)),
            *_names("manufacturer", "name", "display"),
        ),
        request="all pages",
    ),
    Endpoint(
        LIBRENMS,
        "devices?type=all",
        "Up/down status of every LibreNMS device, matched to Nautobot devices by id map, hostname or IP",
        LIBRENMS_DEVICE_FIELDS,
        request="GET /api/v0/devices?type=all, X-Auth-Token; the devices array",
        stored_in="librenms_device_status",
    ),
    Endpoint(
        LIBRENMS,
        "devices/<id or hostname>",
        "One device after an alert push (#284)",
        LIBRENMS_DEVICE_FIELDS,
        request="GET /api/v0/devices/<id or hostname>; the first entry of devices; 404 = unknown device",
        stored_in="librenms_device_status (only devices already there)",
    ),
    Endpoint(
        LIBRENMS,
        "POST /api/librenms/alert (inbound)",
        "An alert push from LibreNMS's API transport (#284); only says which device to refresh",
        (
            Field("device_id", ("integer", "string"), rule="LibreNMS device id; JSON, form or query string"),
            Field("hostname", ("string",), rule="when there is no device_id"),
        ),
        request="Authorization: Bearer <API token, operator role>",
        checked=False,
        notes=("The alert's own state is ignored: the status is read from the LibreNMS API.",),
    ),
)

NOT_COVERED = (
    "The location detail panel's live reads from Nautobot (dcim/locations/<id>/ for the ASN, ipam/asns/, "
    "circuits/circuit-terminations/, and dcim/devices/ when the cache is empty): shown as they come, not stored."
)


def endpoint(source: str, path: str) -> Endpoint:
    return next(item for item in CONTRACT if item.source == source and item.path == path)


def json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _accepts(types: tuple[str, ...], actual: str) -> bool:
    return actual in types or (actual == "integer" and "number" in types)


def check(spec: Endpoint, records: list) -> dict:
    """``{"records": n, "mismatches": {field: {"problem": text, "count": n}}}`` for *records*."""
    mismatches: dict[str, dict] = {}

    def note(path: str, problem: str) -> None:
        entry = mismatches.setdefault(f"{path}: {problem}", {"field": path, "problem": problem, "count": 0})
        entry["count"] += 1

    for record in records:
        if not isinstance(record, dict):
            note("(record)", f"is {json_type(record)}, expected object")
            continue
        for item in spec.fields:
            *parents, key = item.path.split(".")
            container = record
            for parent in parents:
                container = container.get(parent) if isinstance(container, dict) else None
            if not isinstance(container, dict):
                continue  # checked with the parent
            if key not in container:
                if item.required:
                    note(item.path, "missing")
                continue
            actual = json_type(container[key])
            if not _accepts(item.types, actual):
                note(item.path, f"is {actual}, expected {type_text(item.types)}")
    return {"records": len(records), "mismatches": list(mismatches.values())}


def check_and_record(source: str, path: str, records: list) -> dict:
    """Check *records* fetched from *path*, log what doesn't match (once per
    call), and store the result for /metrics and /api/contract.  Never raises.

    No records (an incremental sync with no changes) proves nothing: the
    previous result stays.
    """
    try:
        result = check(endpoint(source, path), records)
    except Exception as exc:  # the contract must never stop a sync
        logger.warning("Data contract check for %s %s failed: %s", source, path, exc, exc_info=True)
        return {"records": 0, "mismatches": []}
    if not records:
        return result
    if result["mismatches"]:
        details = "; ".join(f"{m['field']} {m['problem']} ({m['count']})" for m in result["mismatches"][:10])
        more = len(result["mismatches"]) - 10
        logger.warning(
            "Data contract: %s %s does not match in %d records: %s%s (see docs/data-contract.md)",
            source,
            path,
            result["records"],
            details,
            f"; and {more} more" if more > 0 else "",
        )
    _store(source, path, result)
    return result


def _store(source: str, path: str, result: dict) -> None:
    conn = None
    try:
        conn = db.get_conn()
        if conn is None:
            return
        with db.transaction(conn):
            # One writer per endpoint at a time (e.g. LibreNMS pushes at once).
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (db.advisory_lock_key(f"contract:{source}:{path}"),))
            previous = conn.execute(
                "SELECT count(*) AS n FROM contract_checks WHERE source = %s AND endpoint = %s AND field <> ''",
                (source, path),
            ).fetchone()
            conn.execute("DELETE FROM contract_checks WHERE source = %s AND endpoint = %s", (source, path))
            # One row per endpoint even when all is well: when it was last checked.
            conn.execute(
                "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
                "VALUES (%s, %s, '', '', 0, %s)",
                (source, path, result["records"]),
            )
            for mismatch in result["mismatches"]:
                conn.execute(
                    "INSERT INTO contract_checks (source, endpoint, field, problem, mismatched, records) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (source, path, mismatch["field"], mismatch["problem"], mismatch["count"], result["records"]),
                )
        if db.row_to_dict(previous).get("n") and not result["mismatches"]:
            logger.info("Data contract: %s %s matches again", source, path)
    except Exception as exc:
        logger.warning("Could not store the data contract check for %s %s: %s", source, path, exc)
    finally:
        if conn is not None:
            conn.close()


def latest_checks(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT source, endpoint, field, problem, mismatched, records, checked_at FROM contract_checks "
        "ORDER BY source, endpoint, field, problem"
    ).fetchall()
    checks: dict[tuple, dict] = {}
    for row in map(db.row_to_dict, rows):
        entry = checks.setdefault(
            (row["source"], row["endpoint"]),
            {"source": row["source"], "endpoint": row["endpoint"], "records": row["records"], "mismatches": []},
        )
        if row["field"]:
            entry["mismatches"].append({"field": row["field"], "problem": row["problem"], "count": row["mismatched"]})
        else:
            entry["checked_at"] = row["checked_at"]
    return list(checks.values())


def as_json() -> list[dict]:
    return [
        {
            "source": item.source,
            "endpoint": item.path,
            "used_for": item.used_for,
            "request": item.request,
            "stored_in": item.stored_in,
            "checked": item.checked,
            "notes": list(item.notes),
            "fields": [
                {
                    "path": f.path,
                    "types": list(f.types),
                    "required": f.required,
                    "becomes": f.becomes,
                    "rule": f.rule,
                }
                for f in item.fields
            ],
        }
        for item in CONTRACT
    ]


def type_text(types: tuple[str, ...]) -> str:
    """ "object, string or null": null last."""
    ordered = [t for t in types if t != "null"] + [t for t in types if t == "null"]
    return ", ".join(ordered[:-1]) + " or " + ordered[-1] if len(ordered) > 1 else ordered[0]


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|") or "—"


def render_markdown() -> str:
    """docs/data-contract.md."""
    lines = [
        "# Data contract",
        "",
        "<!-- Generated from nautobot_maps/contract.py: python -m nautobot_maps contract-docs > docs/data-contract.md -->",
        "",
        "What Nautobot Maps reads from Nautobot and LibreNMS, which fields it uses, their types, and what each "
        "becomes in its own tables. Every sync checks the records it fetched against this: a **required** field "
        "that is missing, or any field with another type, is logged once per sync "
        '("Data contract: nautobot dcim/devices/ does not match in 340 records: primary_ip4 is string, '
        'expected object or null (12)"), counted in `/metrics` '
        "(`nautobot_maps_contract_mismatches{source,endpoint,field}`) and shown by `GET /api/contract`. "
        "A mismatch is a warning: the sync goes on with what it can use.",
        "",
        "After a Nautobot or LibreNMS upgrade, look there first. CI checks this contract against a real Nautobot "
        "on every change.",
        "",
        "Types are JSON types. A nested field (`status.id`) is checked only when its parent is an object. "
        "Nested objects can be *brief* (Nautobot 3.x at depth 0: only `id` and `url`); the name then comes from "
        "the lookup endpoints at the end.",
        "",
    ]
    for source, title in ((NAUTOBOT, "Nautobot"), (LIBRENMS, "LibreNMS")):
        lines += [f"## {title}", ""]
        for item in (e for e in CONTRACT if e.source == source):
            lines += [f"### `{item.path}`", "", item.used_for + ".", ""]
            if item.request:
                lines.append(f"- Request: {item.request}")
            if item.stored_in:
                lines.append(f"- Stored in: `{item.stored_in}`")
            if not item.checked:
                lines.append("- Documented only (an inbound request; validated by the endpoint itself)")
            lines += [f"- {note}" for note in item.notes]
            lines += [
                "",
                "| Field | Type | Required | Becomes | Rule |",
                "|---|---|---|---|---|",
            ]
            for f in item.fields:
                lines.append(
                    f"| `{f.path}` | {type_text(f.types)} | {'yes' if f.required else ''} | "
                    f"{_cell(f.becomes)} | {_cell(f.rule)} |"
                )
            lines.append("")
    lines += ["## Not covered", "", NOT_COVERED, ""]
    return "\n".join(lines)
