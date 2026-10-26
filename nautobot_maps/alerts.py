"""Alert logic (#165): severity scoring, the LibreNMS status merge, alert
history, the alert board build and the location detail for the map.

Called as ``alerts.function()`` so tests can replace it on this module.
"""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import requests

from nautobot_maps import (
    caching,
    db,
    inventory,
    librenms,
    maintenance,
    nautobot,
    notify,
    settings,
    timeutil,
    webhooks,
    wireless,
)
from nautobot_maps.repositories import board as board_store

logger = logging.getLogger(__name__)

# Kept here for the routes and MCP that read the feed (#424).
FEED_KINDS = board_store.FEED_KINDS


# In a status exclusion list, this keyword matches a missing or empty status.
NULL_STATUS_KEYWORD = "null"


def status_is_excluded(status: str | None, excluded: set[str]) -> bool:
    """Return whether *status* is in *excluded* (lower-cased; ``null`` matches no status)."""
    normalized = (status or "").strip().lower()
    if not normalized:
        return NULL_STATUS_KEYWORD in excluded
    return normalized in excluded


# ---------------------------------------------------------------------------
# NOC alert helpers
# ---------------------------------------------------------------------------

# Device statuses that count as "down" for alert purposes
# "flapping": a device flapping (#286) counts as down while momentarily up.
DOWN_STATUSES: frozenset = frozenset({"offline", "failed", "decommissioning", "flapping"})


def alert_level_definitions() -> dict:
    """What each level means, from the configured thresholds (#293); shown on
    the board.  They describe compute_alert_level() and must match it."""
    medium = settings.ALERT_MEDIUM_PERCENT
    critical = settings.ALERT_CRITICAL_PERCENT
    critical_share = "every monitored device is down" if critical >= 100 else f"{critical}% or more of them are down"
    return {
        "alarms": "Sites at Critical, Medium or Low: the ones that need attention. The board opens here.",
        "critical": (
            "At least one core device is down (its role matches a critical keyword, "
            "it is marked critical by an override, or set critical in Nautobot or by the site's tier), "
            f"or {critical_share}."
        ),
        "medium": (
            f"More than {medium}% of the site's monitored devices are down, or a device set to Medium "
            "(in Nautobot or by the site's tier) is down, and no core device is down."
        ),
        "low": f"At least one monitored device is down, but {medium}% or fewer, and none set higher.",
        "no_data": "The site has no monitored devices, or its state could not be computed. Not counted as an alert.",
        "ok": "The site has monitored devices and none of them is down.",
        "total": "All sites on the board. Devices with a Nautobot primary IP are monitored, and access points "
        "a wireless controller lists.",
        "maintenance": "Sites in a maintenance window: planned work, not an alarm. Nothing is notified until it ends.",
        "wireless": (
            f"Sites with only access points down, as a wireless controller reports them, at "
            f"{settings.WIRELESS_AP_LEVEL.capitalize()}: not an alarm, never notified."
        ),
    }


# Board order, most severe first (#124).
ALERT_LEVEL_ORDER = ("critical", "medium", "low", "wireless", "no_data", "ok")
# Levels that count as an active alert in summary["non_ok"].
ALERT_LEVELS_NON_OK = ("critical", "medium", "low")


# What a down member passes to its stack's lead (#405): its status, and who
# says it is down and since when.
STACK_DOWN_FIELDS = ("status", "down_source", "webhook_since", "webhook_summary", "last_updated")


def stack_groups(devices: list) -> list[tuple[dict, list, dict | None]]:
    """The stacks among *devices* (#405), each ``(lead, others, down)``: a
    stack of two or more is one logical device, its IPs the stack's.  The lead
    stands for it: its master, else its first member by place (the map
    panel's rule, given the monitored devices).  *down* is the member that
    makes it down, the lead first; None when every member is up."""
    chassis: dict[str, list] = {}
    for device in devices:
        if device.get("vc_id"):
            chassis.setdefault(device["vc_id"], []).append(device)
    groups = []
    for members in chassis.values():
        if len(members) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda d: (not d.get("vc_master"), d.get("vc_position") is None, d.get("vc_position") or 0),
        )
        down = next((d for d in ordered if (d.get("status") or "").lower().strip() in DOWN_STATUSES), None)
        groups.append((ordered[0], ordered[1:], down))
    return groups


def merge_stacks(devices: list) -> list:
    """*devices* (the monitored ones) with each stack as one device, its lead
    (#405): any of its IPs not answering means the whole stack is down, so a
    down member makes the lead down, and the others aren't counted."""
    replace: dict[int, dict] = {}
    dropped: set[int] = set()
    for lead, others, down in stack_groups(devices):
        dropped.update(id(d) for d in others)
        if down is not None and down is not lead:
            replace[id(lead)] = {**lead, **{key: down[key] for key in STACK_DOWN_FIELDS if key in down}}
    return [replace.get(id(d), d) for d in devices if id(d) not in dropped]


def board_stacks(stacks: dict[str, list[dict]]) -> dict[str, dict]:
    """Each stack's ``members`` for one board build, worked out once (#405):
    those of an excluded status, such as Decommissioning, left out; and their
    ``names``.  Only stacks of two or more."""
    excluded = settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES
    result = {}
    for vc_id, all_members in stacks.items():
        members = [m for m in all_members if not status_is_excluded(m["status"], excluded)]
        if len(members) >= 2:
            result[vc_id] = {"members": members, "names": [m["name"] for m in members]}
    return result


def stack_of(device: dict, stacks: dict[str, dict], counted: bool = True) -> dict:
    """A down stack, for the board, Copy and MCP (#402, #405): ``name``,
    ``size``, ``position``, ``master``, and ``whole``: on the board a stack is
    one device (``merge_stacks``), so down and *counted* (not in maintenance,
    not just flapping) it is down with every member; then ``members`` lists
    their names.  {} when it isn't in a stack of two or more."""
    stack = stacks.get(device.get("vc_id") or "")
    if not stack:
        return {}
    whole = counted and device.get("down_source") != "flapping"
    return {
        "name": device.get("vc_name") or "",
        "position": device.get("vc_position"),
        "size": len(stack["members"]),
        "master": bool(device.get("vc_master")),
        "whole": whole,
        **({"members": stack["names"]} if whole else {}),
    }


def board_device_rules() -> dict:
    """Which devices count, wherever a level is worked out (#316): those with
    a primary IP or a wireless controller (#521), not of an excluded status.
    The board, the map's marker colours and the site panel all use this."""
    return {
        "require_primary_ip": True,
        "excluded_device_statuses": settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES,
    }


def not_monitored_reason(device: dict) -> str:
    """Why *device* doesn't count towards its site's level ("" when it does), for the site panel (#316)."""
    if not is_monitored(device):
        # An access point no controller lists (#521): say where else to look.
        if wireless.configured() and wireless.is_access_point(device):
            return "no primary IP, not in a wireless controller"
        return "no primary IP"
    status = (device.get("status") or "").strip()
    if status_is_excluded(status, settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES):
        return f"status {status or 'empty'} is excluded"
    return ""


def device_has_primary_ip(device: dict) -> bool:
    return bool((device.get("primary_ip") or "").strip())


def is_monitored(device: dict) -> bool:
    """Something watches it: a primary IP (LibreNMS, Alertmanager, a webhook),
    or a wireless controller that lists it (``wireless.attach``, #521): access
    points get their address by DHCP, so Nautobot seldom has one."""
    return device_has_primary_ip(device) or bool(device.get("wireless"))


def device_display_ip(device: dict) -> str:
    """Return the address to show for *device* on the alert board.

    Prefers the Nautobot primary IP cached at sync (IPv4 before IPv6), without
    its prefix length.  Falls back to the address a wireless controller reports
    (#521; never written to Nautobot), the address LibreNMS polls (its
    ``overwrite_ip`` or ``ip``), then to the matched LibreNMS hostname when that
    is itself an IP address.  Returns ``""`` when none is known.
    """
    primary_ip = (device.get("primary_ip") or "").strip()
    if primary_ip:
        return primary_ip.split("/", 1)[0]
    wireless_ip = ((device.get("wireless") or {}).get("ip") or "").strip()
    if wireless_ip:
        return wireless_ip
    librenms_ip = (device.get("librenms_ip") or "").strip()
    if librenms_ip:
        return librenms_ip
    librenms_hostname = (device.get("librenms_hostname") or "").strip()
    if inventory.is_ip_literal(librenms_hostname):
        return inventory.librenms_ip_key(librenms_hostname)
    return ""


def location_is_excluded_from_alert_board(location: dict) -> bool:
    name = (location.get("name") or "").strip().lower()
    location_type = (location.get("location_type") or "").strip().lower()
    tags = set()
    for tag in location.get("tags") or []:
        if isinstance(tag, dict):
            tag_name = nautobot.nested_str(tag, "name", "display", "label", "value")
        else:
            tag_name = str(tag).strip()
        if tag_name:
            tags.add(tag_name.strip().lower())
    return bool(
        (name and name in settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES)
        or status_is_excluded(location.get("status"), settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES)
        or (location_type and location_type in settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES)
        or (tags & settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS)
    )


# ---------------------------------------------------------------------------
# Configurable critical-role keyword system
# ---------------------------------------------------------------------------
# Built-in defaults – used when no overrides are configured.
DEFAULT_CORE_ROLE_KEYWORDS: tuple = ("core", "spine", "distribution", "router", "gateway")

# CRITICAL_ROLE_KEYWORDS env var (comma-separated) replaces the built-in
# defaults for every location type that has no specific rule in the JSON file.
env_keywords_raw = settings.CRITICAL_ROLE_KEYWORDS
ENV_CORE_ROLE_KEYWORDS: tuple = (
    tuple(kw.strip().lower() for kw in env_keywords_raw.split(",") if kw.strip())
    if env_keywords_raw
    else DEFAULT_CORE_ROLE_KEYWORDS
)

# Per-location-type rules loaded from the JSON file (if configured).
# Schema: {"<location_type_lower>": ["kw1", "kw2", ...], "default": [...]}
CRITICALITY_RULES: dict = {}
if settings.CRITICALITY_RULES_FILE:
    try:
        with open(settings.CRITICALITY_RULES_FILE, encoding="utf-8") as _f:
            _loaded = json.load(_f)
        if isinstance(_loaded, dict):
            CRITICALITY_RULES = {k.lower(): [kw.lower() for kw in v] for k, v in _loaded.items() if isinstance(v, list)}
            logger.info(
                "Loaded criticality rules from %s: %s",
                settings.CRITICALITY_RULES_FILE,
                list(CRITICALITY_RULES.keys()),
            )
        else:
            logger.warning(
                "Criticality rules file %s must contain a JSON object; ignoring.",
                settings.CRITICALITY_RULES_FILE,
            )
    except PermissionError:
        logger.error(
            "Could not load criticality rules: %s; using CRITICAL_ROLE_KEYWORDS",
            settings.unreadable_hint(settings.CRITICALITY_RULES_FILE),
        )
    except Exception as exc:
        logger.warning("Could not load criticality rules from %s: %s", settings.CRITICALITY_RULES_FILE, exc)


def get_critical_keywords(location_type: str | None = None) -> tuple:
    """Return the critical-role keyword set for *location_type*.

    Resolution order:
    1. Per-location-type entry in *CRITICALITY_RULES* (from the JSON file).
    2. ``"default"`` entry in *CRITICALITY_RULES*.
    3. *ENV_CORE_ROLE_KEYWORDS* (from ``CRITICAL_ROLE_KEYWORDS`` env var, or
       the built-in defaults if the env var is not set).
    """
    if location_type and CRITICALITY_RULES:
        lt_key = location_type.lower()
        if lt_key in CRITICALITY_RULES:
            return tuple(CRITICALITY_RULES[lt_key])
        if "default" in CRITICALITY_RULES:
            return tuple(CRITICALITY_RULES["default"])
    return ENV_CORE_ROLE_KEYWORDS


def is_core_device(device: dict, core_keywords, override_map: dict) -> bool:
    """A core device: marked critical by an override, else its role has a core keyword."""
    device_id = device.get("id") or ""
    if device_id in override_map:
        return bool(override_map[device_id])
    role = (device.get("role") or "").lower()
    return any(keyword in role for keyword in core_keywords)


def down_core_devices(devices: list, location_type: str | None = None, override_map: dict | None = None) -> list:
    """The down core devices among *devices* (#285): the likely cause of the
    other devices down at the same site."""
    keywords = get_critical_keywords(location_type)
    overrides = override_map
    if overrides is None:
        overrides = {}
        conn = db.get_conn()
        if conn is not None:
            try:
                ids = [d.get("id") for d in devices if d.get("id")]
                if ids:
                    overrides = board_store.read_criticality_overrides(conn, ids)
            except Exception as exc:
                logger.debug("Could not read criticality overrides: %s", exc)
            finally:
                conn.close()
    return [
        device
        for device in devices
        if (device.get("status") or "").lower().strip() in DOWN_STATUSES and is_core_device(device, keywords, overrides)
    ]


# Why a device has its level (#377), in the board's reason line.
LEVEL_SOURCE_TEXT = {"nautobot": "set in Nautobot", "override": "override", "role": "core role"}


def device_level(device: dict, core_keywords, override_map: dict, tier: str = "") -> tuple[str, str]:
    """``(level, source)``: what this device makes its site when down (#377).

    The device's Nautobot custom field (``nautobot``) wins, in either
    direction.  Else the worst of: the app's override (``override``: critical;
    "not critical" only cancels the role), a core role keyword (``role``), the
    site tier's default from TIER_DEVICE_LEVEL (``tier``), which so never
    lowers a core device.  ``("", "")``: only the share of devices down counts.

    An access point a wireless controller watches (#521): its Nautobot field,
    else the override's critical, else WIRELESS_AP_LEVEL (``wireless``), never
    the tier's or a role's: at 10,000 APs single ones drop all day.
    """
    if device.get("criticality") in settings.DEVICE_LEVELS:
        return device["criticality"], "nautobot"
    device_id = device.get("id") or ""
    if device.get("wireless"):
        if override_map.get(device_id):
            return "critical", "override"
        return settings.WIRELESS_AP_LEVEL, "wireless"
    if device_id in override_map:
        own = ("critical", "override") if override_map[device_id] else ("", "")
    else:
        role = (device.get("role") or "").lower()
        own = ("critical", "role") if any(keyword in role for keyword in core_keywords) else ("", "")
    tier_level = tier_device_level(tier)
    if notify.LEVEL_RANK.get(tier_level, 0) > notify.LEVEL_RANK.get(own[0], 0):
        return tier_level, "tier"
    return own


def _devices_reason(level: str, names: list, sources: list, tier: str) -> str:
    """ "Core device(s) offline: core01", or with why: "(set in Nautobot)", "(gold site)"."""
    listed = ", ".join(names[:3])
    suffix = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
    if level == "critical" and set(sources) <= {"override", "role"}:
        return f"Core device(s) offline: {listed}{suffix}"  # as before #377
    why = ", ".join(
        dict.fromkeys(f"{tier.strip().lower()} site" if src == "tier" else LEVEL_SOURCE_TEXT[src] for src in sources)
    )
    return f"{level.capitalize()} device(s) offline: {listed}{suffix} ({why})"


def compute_alert_level(
    devices: list,
    location_type: str | None = None,
    override_map: dict | None = None,
    tier: str = "",
) -> dict:
    """Return the NOC alert level for a location based on its device list.

    Returns a dict::

        {"level": "critical" | "medium" | "low" | "no_data" | "ok", "reason": "<human-readable text>"}

    Rules (#124, #377): the worst of
    * the down devices' own levels (``device_level``: the Nautobot custom
      field, the override, the site *tier*'s default, a core role keyword);
    * every device down: **critical**;
    * the share down: **critical** at ALERT_CRITICAL_PERCENT, **medium**
      above ALERT_MEDIUM_PERCENT, else **low**.
    * **no_data**  – there are no devices to judge.
    * **ok**       – devices present, none down.

    Access points a wireless controller watches (#521) are judged on their
    own, left out of the share and of "every device down", so 40 APs never
    make one switch of six a smaller share.  One with a level set (Nautobot,
    override) gives it, like any device.  The others give WIRELESS_AP_LEVEL
    only when nothing else is down: then the answer has ``"wireless_only":
    True``, the board's Wireless chip, not an alarm, never notified.

    The optional *location_type* parameter selects the matching keyword set
    when location-type-scoped rules are configured (e.g. "datacenter" vs
    "office").

    *override_map* (``{device_id: is_critical}``) skips the database read of
    the per-device overrides; the alert board passes one it loaded for all
    sites at once (#149).
    """
    if not devices:
        return {"level": "no_data", "reason": "No monitored devices"}

    core_keywords = get_critical_keywords(location_type)

    if override_map is None:
        override_map = {}
        conn = db.get_conn()
        if conn is not None:
            try:
                ids = [d.get("id") for d in devices if d.get("id")]
                if ids:
                    override_map = board_store.read_criticality_overrides(conn, ids)
            except Exception as exc:
                logger.debug("Could not read criticality overrides: %s", exc)
            finally:
                conn.close()

    wired = [device for device in devices if not device.get("wireless")]
    alert = _wired_level(wired, core_keywords, override_map, tier) if wired else {"level": "ok", "reason": ""}
    down_aps = [
        device
        for device in devices
        if device.get("wireless") and (device.get("status") or "").lower().strip() in DOWN_STATUSES
    ]
    if not down_aps:
        return alert
    levels = [device_level(device, core_keywords, override_map, tier) for device in down_aps]
    # Set in Nautobot or marked critical: an alarm like any device's.
    own = [
        (device, level, source)
        for device, (level, source) in zip(down_aps, levels, strict=True)
        if source != "wireless"
    ]
    if own:
        worst = max((level for _, level, _ in own), key=lambda level: notify.LEVEL_RANK[level])
        if notify.LEVEL_RANK[worst] > notify.LEVEL_RANK.get(alert["level"], 0):
            names = [device.get("name") or "Unknown" for device, level, _ in own if level == worst]
            sources = [source for _, level, source in own if level == worst]
            return {"level": worst, "reason": _devices_reason(worst, names, sources, tier)}
        return alert
    # Only at the wireless level: the site's level only when nothing else is
    # down; a site already in alarm just counts them (#521).
    if alert["level"] != "ok":
        return alert
    count = len(down_aps)
    return {
        "level": settings.WIRELESS_AP_LEVEL,
        "reason": f"{count} access point{'s' if count != 1 else ''} offline",
        "wireless_only": True,
    }


def _wired_level(devices: list, core_keywords, override_map: dict, tier: str) -> dict:
    """``compute_alert_level`` of the devices other than controller-watched access points."""
    down_count = 0
    # The worst level the down devices give, and which of them give it.
    device_worst, worst_names, worst_sources = "", [], []
    for device in devices:
        status = (device.get("status") or "").lower().strip()
        if status not in DOWN_STATUSES:
            continue
        down_count += 1
        level, source = device_level(device, core_keywords, override_map, tier)
        if not level:
            continue
        rank, worst_rank = notify.LEVEL_RANK[level], notify.LEVEL_RANK.get(device_worst, 0)
        if rank > worst_rank:
            device_worst, worst_names, worst_sources = level, [], []
        if rank >= worst_rank:
            worst_names.append(device.get("name") or "Unknown")
            worst_sources.append(source)

    if device_worst == "critical":
        return {"level": "critical", "reason": _devices_reason("critical", worst_names, worst_sources, tier)}

    total = len(devices)
    if down_count == total:
        return {
            "level": "critical",
            "reason": f"All {total} monitored device{'s' if total != 1 else ''} offline",
        }
    if not down_count:
        return {"level": "ok", "reason": ""}
    pct = round(down_count / total * 100)
    share = f"{down_count}/{total} devices offline ({pct}%)"
    # Thresholds from the settings (#293), read now so a test or a reload sees them.
    if down_count * 100 >= total * settings.ALERT_CRITICAL_PERCENT:
        share_level = "critical"
    elif down_count * 100 > total * settings.ALERT_MEDIUM_PERCENT:
        share_level = "medium"
    else:
        share_level = "low"
    if notify.LEVEL_RANK.get(device_worst, 0) > notify.LEVEL_RANK[share_level]:
        return {"level": device_worst, "reason": _devices_reason(device_worst, worst_names, worst_sources, tier)}
    return {"level": share_level, "reason": share}


def device_kinds(counted: list[dict], down: list[dict]) -> dict:
    """How many of *counted* are access points (a role in ACCESS_POINT_ROLES,
    or one a wireless controller lists, #521) and how many network equipment
    (#499), and the same of *down*, those down now (#503).  Only counts: the
    status page shows no more."""

    def access_points(devices: list[dict]) -> int:
        return sum(bool(d.get("wireless")) or wireless.is_access_point(d) for d in devices)

    total, down_access_points = access_points(counted), access_points(down)
    return {
        "access_point_count": total,
        "network_device_count": len(counted) - total,
        "down_access_point_count": down_access_points,
        "down_network_device_count": len(down) - down_access_points,
    }


def tenant_scoped_alerts(
    scopes: dict,
    tenants: list[str],
    devices: list[dict],
    pending_devices: list[dict],
    maintenance_devices: list[dict],
    site_window: dict | None,
    checked_at: str,
    location_type: str | None,
    override_map: dict,
    tier: str,
) -> dict:
    """Levels for configured tenants, reusing the site's already-monitored devices (#410).

    *devices* are those the site's level counts; *maintenance_devices* are left
    out of it, as on the board, but still the tenant's: with only those, the
    tenant sees maintenance, not a hidden site."""
    tenant_names = {tenant.strip().casefold() for tenant in tenants if tenant}
    scoped = {}
    for tenant, roles in scopes.items():
        if tenant not in tenant_names:
            continue
        role_set = set(roles)

        def theirs(group: list[dict], role_set=role_set) -> list[dict]:
            return [device for device in group if (device.get("role") or "").strip().casefold() in role_set]

        active, pending, in_maintenance = theirs(devices), theirs(pending_devices), theirs(maintenance_devices)
        matches = len(active) + len(pending) + len(in_maintenance)
        alert = {"level": "ok" if pending else "no_data"}
        if active:
            alert = compute_alert_level(active, location_type, override_map=override_map, tier=tier)
        level = alert["level"]
        if matches and (site_window is not None or (in_maintenance and not active)):
            level = "maintenance"
        downtime = max(
            (
                int(seconds_between(device.get("first_seen_down_at") or down_since(device, checked_at), checked_at))
                for device in active
                if (device.get("status") or "").lower().strip() in DOWN_STATUSES
            ),
            default=0,
        )
        # Its own devices only, never a shared site's other tenants' (#503);
        # one in maintenance counts but isn't down, nor any in a site window.
        down = [d for d in active if (d.get("status") or "").lower().strip() in DOWN_STATUSES]
        kinds = device_kinds([*active, *pending, *in_maintenance], [] if level == "maintenance" else down)
        scoped[tenant] = {
            "alert_level": level,
            # Only access points down (#521): not an alarm for this tenant either.
            **({"wireless_only": True} if alert.get("wireless_only") and level != "maintenance" else {}),
            "device_count": matches,
            "current_downtime_seconds": max(0, downtime),
            **kinds,
        }
    return scoped


def librenms_index(lnms_devices: list) -> dict:
    """Look-ups into the LibreNMS inventory by device id, hostname and IP (#240).

    Built once per alert-board build, not once per site.  Hostnames and IPs
    are kept apart so IP-valued hostnames are never short-name normalized
    into ambiguous keys such as "10".
    """
    by_id: dict = {}
    by_hostname: dict = {}
    by_ip: dict = {}
    for ld in lnms_devices:
        if ld.get("device_id") is not None:
            by_id.setdefault(ld.get("device_id"), ld)
        hostname = (ld.get("hostname") or "").strip()
        if hostname:
            if inventory.is_ip_literal(hostname):
                by_ip[inventory.librenms_ip_key(hostname)] = ld
            else:
                by_hostname[inventory.librenms_host_key(hostname)] = ld
    return {"by_id": by_id, "by_hostname": by_hostname, "by_ip": by_ip}


def enrich_with_librenms(
    devices: list,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
    snapshot_only: bool = False,
    lnms_index: dict | None = None,
    new_librenms_maps: dict | None = None,
) -> list:
    """Merge live LibreNMS status into *devices* (in-place copy returned).

    Each device is matched to a LibreNMS device by its persisted mapping,
    then by hostname, then by primary IP.  New hostname/IP matches are
    persisted in ``librenms_device_map``: added to *new_librenms_maps* when
    given (an alert-board build writes them all at once, #240), otherwise
    written at the end of this call on one connection.

    LibreNMS ``status`` field: ``1`` = up, ``0`` = down.  When LibreNMS
    reports a device as down but Nautobot has it as active, the status is
    set to ``"offline"`` so ``compute_alert_level`` counts it as down.

    The enrichment is *additive*: Nautobot status is never upgraded (a device
    already offline in Nautobot stays offline regardless of LibreNMS).
    """
    if not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        return devices

    if lnms_index is None:
        if lnms_devices is None:
            lnms_devices = inventory.read_librenms_devices()
        if not lnms_devices and not snapshot_only:
            inventory.ensure_snapshot()
            lnms_devices = inventory.read_librenms_devices()
        if not lnms_devices and not snapshot_only:
            try:
                lnms_devices = librenms.fetch_inventory()
            except Exception as exc:
                logger.warning("LibreNMS enrichment failed (could not fetch devices): %s", exc)
                return devices
        if not lnms_devices:
            return devices
        lnms_index = librenms_index(lnms_devices)
    if not lnms_index["by_id"] and not lnms_index["by_hostname"] and not lnms_index["by_ip"]:
        return devices

    # Load Nautobot UUID → LibreNMS device ID overrides from DB
    if lnms_id_map is None:
        lnms_id_map = board_store.load_librenms_id_map()

    new_maps = new_librenms_maps if new_librenms_maps is not None else {}
    enriched = []
    for device in devices:
        device = dict(device)
        nautobot_id = device.get("id", "")
        lnms_record = None

        # 1. The persisted ID mapping first
        if nautobot_id in lnms_id_map:
            lnms_record = lnms_index["by_id"].get(lnms_id_map[nautobot_id]["device_id"])

        # 2. Fall back to hostname, then IP
        if lnms_record is None:
            lnms_record = lnms_index["by_hostname"].get(inventory.librenms_host_key(device.get("name") or ""))
            if lnms_record is None:
                primary_ip = inventory.librenms_ip_key(device.get("primary_ip") or "")
                if primary_ip:
                    lnms_record = lnms_index["by_ip"].get(primary_ip)

        if lnms_record is not None:
            device["librenms_hostname"] = lnms_record.get("hostname") or ""
            device["librenms_ip"] = inventory.librenms_polled_ip(lnms_record)
            lnms_status = lnms_record.get("status")
            if lnms_status == 0:
                # LibreNMS says down – mark as offline if not already a down status
                current = (device.get("status") or "").lower()
                if current not in DOWN_STATUSES:
                    device["status"] = "offline"
                    # Nautobot still says up, so its last_updated is not when this outage began (#166).
                    device["down_source"] = "librenms"
                    logger.debug(
                        "LibreNMS enrichment: device %s marked offline (LibreNMS status=0)",
                        device.get("name"),
                    )
            # Remember a mapping resolved by hostname or IP
            if nautobot_id and nautobot_id not in lnms_id_map:
                lnms_id = lnms_record.get("device_id")
                if lnms_id:
                    new_maps[nautobot_id] = (lnms_id, lnms_record.get("hostname", ""))

        enriched.append(device)
    if new_librenms_maps is None and new_maps:
        board_store.store_librenms_maps(new_maps)
    return enriched


def fetch_live_location_devices(location_id: str) -> list:
    """Fetch devices for one location, handling Nautobot filter-key variants."""
    try:
        return nautobot.fetch_all_pages("dcim/devices/", {"location_id": location_id, "depth": 1})
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code != 400:
            raise
    return nautobot.fetch_all_pages("dcim/devices/", {"location": location_id, "depth": 1})


def enrich_with_webhooks(devices: list, index: dict) -> list:
    """Mark devices down that Alertmanager or a webhook says are (#292).

    Additive, as LibreNMS: a device Nautobot already has down stays as it is.
    """
    if not index or not (index["by_host"] or index["by_ip"]):
        return devices
    enriched = []
    for device in devices:
        alert = webhooks.find(index, device)
        if alert is not None and (device.get("status") or "").lower() not in DOWN_STATUSES:
            device = dict(device)
            device["status"] = "offline"
            device["down_source"] = alert["source"]
            device["webhook_since"] = alert["started_at"]
            device["webhook_summary"] = alert["summary"]
        enriched.append(device)
    return enriched


def enrich_with_wireless(devices: list) -> list:
    """Mark devices down that their wireless controller says are (#521).

    Additive, as LibreNMS: a device already down stays as it is.  Since when:
    when a board build first saw it down (``down_since``)."""
    enriched = []
    for device in devices:
        ap = device.get("wireless")
        if ap and not ap["up"] and (device.get("status") or "").lower().strip() not in DOWN_STATUSES:
            device = {**device, "status": "offline", "down_source": "wireless"}
        enriched.append(device)
    return enriched


def webhook_reason(device: dict) -> str:
    """ "alertmanager: Target down": who says the device is down, and why (#292)."""
    summary = device.get("webhook_summary") or ""
    return f"{device.get('down_source')}: {summary}" if summary else str(device.get("down_source") or "")


def get_location_devices_and_alert(
    location_id: str,
    location_type: str | None = None,
    devices_data: list | None = None,
    devices_already_normalized: bool = False,
    lookup_maps: dict | None = None,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
    snapshot_only: bool = False,
    require_primary_ip: bool = False,
    override_map: dict | None = None,
    excluded_device_statuses: set[str] | None = None,
    lnms_index: dict | None = None,
    new_librenms_maps: dict | None = None,
    webhook_index: dict | None = None,
    tier: str = "",
    wireless_index: dict | None = None,
) -> tuple[list, dict]:
    """Return ``(devices, alert)`` for a location (*tier*: the site's, #377).

    Devices whose Nautobot status is in *excluded_device_statuses* are dropped
    before scoring and before the LibreNMS merge (#151).  *wireless_index*
    (``wireless.read_index``; None: read now) marks the access points a
    controller watches (#521).
    """
    use_normalized_devices = devices_already_normalized
    if devices_data is None:
        devices_data = inventory.read_devices(location_id)
        if devices_data:
            use_normalized_devices = True
            if not snapshot_only:
                inventory.ensure_snapshot()
        elif not snapshot_only:
            inventory.ensure_snapshot()
            devices_data = inventory.read_devices(location_id)
            if devices_data:
                use_normalized_devices = True
            else:
                devices_data = fetch_live_location_devices(location_id)

    devices = (
        [
            {
                "id": d.get("id") or "",
                "name": d.get("name") or "Unknown",
                "device_type": d.get("device_type", ""),
                "manufacturer": d.get("manufacturer", ""),
                "role": d.get("role", ""),
                "status": d.get("status", ""),
                "primary_ip": d.get("primary_ip", ""),
                "platform": d.get("platform", ""),
                "serial": d.get("serial", ""),
                "tenant": d.get("tenant", ""),
                "location_path": d.get("location_path", ""),
                "last_updated": d.get("last_updated", ""),
                "criticality": d.get("criticality", ""),
                "vc_id": d.get("vc_id", ""),
                "vc_name": d.get("vc_name", ""),
                "vc_position": d.get("vc_position"),
                "vc_master": d.get("vc_master", False),
            }
            for d in devices_data
        ]
        if use_normalized_devices
        else inventory.normalize_devices(devices_data, lookup_maps=lookup_maps)
    )
    devices = wireless.attach(devices, wireless.load_index(devices) if wireless_index is None else wireless_index)
    if require_primary_ip:
        devices = [device for device in devices if is_monitored(device)]
    if excluded_device_statuses:
        devices = [
            device for device in devices if not status_is_excluded(device.get("status"), excluded_device_statuses)
        ]

    enriched = enrich_with_librenms(
        devices,
        lnms_devices=lnms_devices,
        lnms_id_map=lnms_id_map,
        snapshot_only=snapshot_only,
        lnms_index=lnms_index,
        new_librenms_maps=new_librenms_maps,
    )
    enriched = enrich_with_webhooks(enriched, webhooks.load_index() if webhook_index is None else webhook_index)
    # After the status filter: a Decommissioning AP stays left out (#521).
    enriched = enrich_with_wireless(enriched)
    if require_primary_ip:
        # Counted as the board counts: a stack is one device (#405).
        enriched = merge_stacks(enriched)
    return enriched, compute_alert_level(enriched, location_type, override_map=override_map, tier=tier)


def tier_device_level(tier: str | None) -> str:
    """The TIER_DEVICE_LEVEL default for this site tier, or "" (#377)."""
    return settings.TIER_DEVICE_LEVEL.get((tier or "").strip().lower(), "")


def tier_rank(tier: str) -> int:
    """0 for the most important tier in SITE_TIERS (#293); 99 without a known tier."""
    tier = (tier or "").strip().lower()
    return settings.SITE_TIERS.index(tier) if tier in settings.SITE_TIERS else 99


def shown_level(level: str, wireless_only: bool) -> str:
    """Where a site goes in the board's summary and order: "wireless" when only
    access points are down (#521), after every alarm; else its level."""
    return "wireless" if wireless_only else level


def alert_sort_key(level: str) -> int:
    level = (level or "").lower()
    return ALERT_LEVEL_ORDER.index(level) if level in ALERT_LEVEL_ORDER else len(ALERT_LEVEL_ORDER)


def resolve_open_alert_instances_for_site(
    conn,
    site_id: str,
    open_alert_keys: set[str],
    checked_at: str,
    resolved_at_by_device: dict[str, str] | None = None,
) -> None:
    """Resolve the site's open alerts not in *open_alert_keys* at *checked_at*,
    or, for a device in *resolved_at_by_device* (flapping ended, #286), when
    it came back up."""
    for row_data in board_store.open_alerts_of_site(conn, site_id):
        alert_key = row_data.get("alert_key", "")
        if alert_key in open_alert_keys:
            continue
        resolved_at = checked_at
        came_up = (resolved_at_by_device or {}).get(row_data.get("device_id") or "")
        if (
            came_up
            and _aware(row_data.get("down_started_at"))
            and _aware(came_up) >= _aware(row_data["down_started_at"])
        ):
            resolved_at = came_up
        started = timeutil.parse_iso_datetime(row_data.get("down_started_at"))
        resolved = timeutil.parse_iso_datetime(resolved_at)
        elapsed = 0
        if started and resolved:
            elapsed = max(0, int((resolved - started).total_seconds()))
        if not board_store.resolve_alert_instance(conn, row_data["id"], resolved_at, elapsed):
            continue
        board_store.insert_alert_event(
            conn=conn,
            instance_id=row_data["id"],
            event_type="resolved",
            event_at=resolved_at,
            alert_level="ok",
            alert_reason="Recovered",
            snapshot={
                "site_id": row_data.get("site_id") or "",
                "site_name": row_data.get("site_name") or "",
                "device_id": row_data.get("device_id") or "",
                "device_name": row_data.get("device_name") or "",
                "status": "resolved",
            },
        )


MAX_ACK_NOTE_LENGTH = 200


def acknowledge(conn, site_id: str, device_ids: list | None, by: str, note: str = "", clear: bool = False) -> dict:
    """Acknowledge (or with *clear*, un-acknowledge) the open alerts at *site_id* (#288).

    *device_ids*: those devices' alerts; ``None`` or empty: every open alert
    of the site.  All or nothing, like cases: a device without an open alert
    leaves everything as it was.  Returns ``{"changed": [device_id],
    "missing": [device_id]}``; each change is an event in the history.
    """
    note = (note or "").strip()[:MAX_ACK_NOTE_LENGTH]
    at = timeutil.iso_utc_now()
    with db.transaction(conn):
        # Locked: a board build resolving one meanwhile waits, so an
        # acknowledgement never lands on an alarm that just ended.
        by_device = {row["device_id"]: row for row in board_store.lock_open_alerts_of_site(conn, site_id)}
        wanted = list(dict.fromkeys(device_ids)) if device_ids else list(by_device)
        missing = [device_id for device_id in wanted if device_id not in by_device]
        if missing:
            return {"changed": [], "missing": missing}
        changed = []
        for device_id in wanted:
            row = by_device[device_id]
            # Idempotent both ways: the first acknowledgement keeps its owner.
            if (row.get("acknowledged_at") is None) == clear:
                continue
            if clear:
                board_store.clear_acknowledgement(conn, row["id"])
            else:
                board_store.set_acknowledgement(conn, row["id"], at, by, note)
            board_store.insert_alert_event(
                conn=conn,
                instance_id=row["id"],
                event_type="unacknowledged" if clear else "acknowledged",
                event_at=at,
                alert_level=row.get("alert_level") or "",
                alert_reason=row.get("alert_reason") or "",
                snapshot={
                    "site_id": site_id,
                    "site_name": row.get("site_name") or "",
                    "device_id": device_id,
                    "device_name": row.get("device_name") or "",
                    "by": by,
                    **({} if clear else {"note": note}),
                },
            )
            changed.append(device_id)
    return {"changed": changed, "missing": []}


def down_since(device: dict, checked_at: str) -> str:
    """When *device* went down, as far as we can tell (#166).

    If Nautobot's status makes it down, that status was set at or before the
    device's ``last_updated``; use it when it is earlier than *checked_at*
    (when this build saw the outage).  ``last_updated`` moves on any edit, so
    the earlier of the two is the safe choice.  Devices only LibreNMS reports
    down keep *checked_at*, or when a build first saw them down
    (``first_seen_down_at``, from ``device_states``, #286), if earlier.
    """
    return earliest_time(_down_since(device, checked_at), device.get("first_seen_down_at"))


def _aware(value):
    parsed = timeutil.parse_iso_datetime(value) if isinstance(value, str) and value else None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def earliest_time(*values) -> str | None:
    """The earliest of ISO times (unparseable or empty ones ignored), as given."""
    known = [(parsed, value) for value in values if (parsed := _aware(value)) is not None]
    return min(known, key=lambda pair: pair[0])[1] if known else None


def seconds_between(start: str, end: str) -> float:
    start_at, end_at = _aware(start), _aware(end)
    return (end_at - start_at).total_seconds() if start_at and end_at else 0.0


# A device_states row no build has seen down for this long is dropped.
DEVICE_STATE_RETENTION = timedelta(days=7)


# Flapping (#286): FLAP_CHANGES up/down changes within this window; it ends
# after a whole window without a change.
FLAP_WINDOW = timedelta(minutes=30)


def _recent(changes: list, now: str) -> list:
    window_start = _aware(now) - FLAP_WINDOW
    return [change for change in changes if (_aware(change) or window_start) > window_start]


def next_device_state(stored: dict | None, is_down: bool, since: str | None, now: str, source: str = "") -> dict | None:
    """A device's state after this build (#286); None: up and steady, no row.

    *since*: when it entered the state it is in now, if that is new (else
    the stored time stays); *source*: what says it is down.  Each up/down
    change is kept for FLAP_WINDOW; with FLAP_CHANGES of them it is
    flapping, until a whole window passes without a change.
    """
    previous = stored["state"] if stored else "up"
    state = "down" if is_down else "up"
    if state == "down":
        since = since or now
    else:
        since = stored["since"] if stored and previous == "up" else (since or now)
    changes = list(stored.get("changes") or []) if stored else []
    flapping_since = stored.get("flapping_since") if stored else None
    if settings.FLAP_CHANGES:
        if state != previous:
            changes.append(since)
        changes = _recent(changes, now)
        if len(changes) >= settings.FLAP_CHANGES:
            flapping_since = flapping_since or now
        elif not changes:
            flapping_since = None
    else:
        changes, flapping_since = [], None
    if state == "up" and not changes and not flapping_since:
        return None
    return {
        "state": state,
        "since": since,
        "changes": changes,
        "flapping_since": flapping_since,
        "source": source if state == "down" else "",
    }


def _same_state(stored: dict, row: dict, now: str) -> bool:
    return (
        stored["state"] == row["state"]
        and (stored.get("source") or "") == row["source"]
        and _aware(stored["since"]) == _aware(row["since"])
        and _aware(stored.get("flapping_since")) == _aware(row["flapping_since"])
        and [_aware(c) for c in _recent(stored.get("changes") or [], now)] == [_aware(c) for c in row["changes"]]
    )


def device_states_to_write(next_states: dict, stored: dict, now: str) -> bool:
    """Whether this build has anything to store: a changed row, a row to
    drop, or rows of devices gone for DEVICE_STATE_RETENTION."""
    for device_id, (_, row) in next_states.items():
        previous = stored.get(device_id)
        if (row is None and previous) or (row is not None and (not previous or not _same_state(previous, row, now))):
            return True
    expired_before = _aware(now) - DEVICE_STATE_RETENTION
    return any(
        device_id not in next_states and (_aware(state.get("updated_at")) or expired_before) < expired_before
        for device_id, state in stored.items()
    )


def write_device_states(conn, next_states: dict, stored: dict, now: str) -> None:
    """Store *next_states* ``{device_id: (site_id, row or None)}`` in one
    transaction, only what changed; drop rows of devices no build has seen
    for DEVICE_STATE_RETENTION (gone from the inventory)."""
    with db.transaction(conn):
        dropped = []
        for device_id, (site_id, row) in next_states.items():
            previous = stored.get(device_id)
            if row is None:
                if previous:
                    dropped.append(device_id)
                continue
            if previous and _same_state(previous, row, now):
                continue
            board_store.upsert_device_state(conn, device_id, site_id, row)
        if dropped:
            board_store.delete_device_states(conn, dropped)
        board_store.delete_unseen_device_states(conn, now, DEVICE_STATE_RETENTION, list(next_states))


def came_up_at(device: dict, stored: dict | None, now: str) -> str:
    """When a device that was down came back up, as far as we can tell (#286).

    Nautobot's status was changed at or before its ``last_updated``: use
    that when Nautobot said it was down and the edit is after the outage
    began.  When LibreNMS said so (or nothing is known): *now*.
    """
    if not stored or stored["state"] != "down" or (stored.get("source") or "") != "nautobot":
        return now
    edited, began, observed = _aware(device.get("last_updated")), _aware(stored["since"]), _aware(now)
    if edited and began and observed and began < edited <= observed:
        return device["last_updated"]
    return now


def _down_since(device: dict, checked_at: str) -> str:
    if device.get("webhook_since"):
        # When the other tool says it went down (#292), not in the future.
        return earliest_time(device["webhook_since"], checked_at) or checked_at
    if device.get("down_source") in ("librenms", "flapping", "wireless"):
        return checked_at
    changed = timeutil.parse_iso_datetime(device.get("last_updated"))
    observed = timeutil.parse_iso_datetime(checked_at)
    if changed is None or observed is None:
        return checked_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    if changed < observed:
        return changed.astimezone(UTC).isoformat()
    return checked_at


def upsert_alert_lifecycle_for_site(
    site: dict,
    devices: list[dict],
    alert: dict,
    checked_at: str,
    conn=None,
    frozen_device_ids: set[str] | None = None,
    resolved_at_by_device: dict[str, str] | None = None,
    probable_causes: dict[str, str] | None = None,
    core_device_ids: set[str] | None = None,
) -> bool:
    """Record the site's down devices as alert instances and resolve recovered ones.

    Open alerts of *frozen_device_ids* (devices in maintenance, #283) are left
    as they are: not updated and not resolved.  Returns ``False`` when the
    write failed (the caller should not reuse *conn*).
    """
    owns_conn = conn is None
    if conn is None:
        conn = db.get_conn()
    if conn is None:
        return False
    site_id = (site.get("id") or "").strip()
    if not site_id:
        if owns_conn:
            conn.close()
        return True
    open_alert_keys: set[str] = {db.build_alert_key(site_id, device_id) for device_id in frozen_device_ids or ()}
    try:
        with db.transaction(conn):
            for device in devices:
                status = (device.get("status") or "").lower().strip()
                if status not in DOWN_STATUSES:
                    continue
                device_id = (device.get("id") or "").strip()
                if not device_id:
                    continue
                level = (alert.get("level") or "no_data").lower()
                reason = alert.get("reason") or ""
                alert_key = db.build_alert_key(site_id, device_id)
                open_alert_keys.add(alert_key)
                current = board_store.latest_open_alert(conn, alert_key)
                if current is None:
                    inserted_id = board_store.open_alert_instance(
                        conn,
                        alert_key,
                        site_id,
                        site.get("name") or "",
                        device_id,
                        device.get("name") or "",
                        level,
                        reason,
                        down_since(device, checked_at),
                        checked_at,
                        (probable_causes or {}).get(device_id, ""),
                        device_id in (core_device_ids or ()),
                        # Left out of availability and the activity feed (#521).
                        access_point=bool(device.get("wireless")),
                    )
                    if inserted_id is not None:
                        board_store.insert_alert_event(
                            conn=conn,
                            instance_id=inserted_id,
                            event_type="opened",
                            event_at=checked_at,
                            alert_level=level,
                            alert_reason=reason,
                            snapshot={
                                "site_id": site_id,
                                "site_name": site.get("name") or "",
                                "device_id": device_id,
                                "device_name": device.get("name") or "",
                                "status": status,
                            },
                        )
                        continue
                    # Opened meanwhile by another build: update that one.
                    current = board_store.latest_open_alert(conn, alert_key)
                    if current is None:
                        continue
                board_store.refresh_open_alert(
                    conn,
                    current["id"],
                    site.get("name") or "",
                    device.get("name") or "",
                    level,
                    reason,
                    checked_at,
                    down_since(device, checked_at),
                    (probable_causes or {}).get(device_id, ""),
                    device_id in (core_device_ids or ()),
                )
                if current.get("alert_level") != level or current.get("alert_reason") != reason:
                    board_store.insert_alert_event(
                        conn=conn,
                        instance_id=current["id"],
                        event_type="updated",
                        event_at=checked_at,
                        alert_level=level,
                        alert_reason=reason,
                        snapshot={
                            "site_id": site_id,
                            "site_name": site.get("name") or "",
                            "device_id": device_id,
                            "device_name": device.get("name") or "",
                            "status": status,
                        },
                    )
            resolve_open_alert_instances_for_site(conn, site_id, open_alert_keys, checked_at, resolved_at_by_device)
        return True
    except Exception as exc:
        logger.warning("Could not persist alert lifecycle for site %s: %s", site_id, exc, exc_info=True)
        return False
    finally:
        if owns_conn:
            conn.close()


def empty_alert_context() -> dict:
    return {
        "active_alert_instance_count": 0,
        "historical_downtime_seconds": 0,
        "current_downtime_seconds": 0,
        "latest_down_at": None,
        "active_cases": [],
        "down_devices": [],
    }


def summarize_alert_context(
    historical_downtime_seconds: int,
    open_rows: list[dict],
    case_numbers_by_instance: dict[int, list[str]],
    checked_at: str,
) -> dict:
    """Build a site's alert context from its alert-instance data.

    *historical_downtime_seconds* is the sum of ``total_downtime_seconds``
    over all the site's instances; *open_rows* are its open instances, newest
    first.  Open instances add the time they have been down so far.
    """
    now_dt = timeutil.parse_iso_datetime(checked_at) or datetime.now(UTC)
    historical_seconds = historical_downtime_seconds
    active_cases: set[str] = set()
    down_devices = []
    current_downtime_seconds = 0
    latest_down_at = None
    for data in open_rows:
        started = timeutil.parse_iso_datetime(data.get("down_started_at"))
        if started is not None:
            elapsed = max(0, int((now_dt - started).total_seconds()))
            historical_seconds += elapsed
            current_downtime_seconds = max(current_downtime_seconds, elapsed)
        latest_down_at = timeutil.max_iso_datetime_value(latest_down_at, data.get("down_started_at"))
        case_numbers = case_numbers_by_instance.get(int(data["id"]), [])
        active_cases.update(case_numbers)
        down_devices.append(
            {
                "device_id": data.get("device_id") or "",
                "device_name": data.get("device_name") or "",
                # When it went down, for "Newest down first" and Copy (#227, #228).
                "down_started_at": data.get("down_started_at") or None,
                "case_numbers": case_numbers,
                # Kept for when a build can't observe the site (#285).
                "stored_probable_cause": data.get("probable_cause") or "",
                "stored_core": bool(data.get("is_core")),
                # Someone is on it (#288).
                **(
                    {
                        "acknowledged": {
                            "by": data.get("acknowledged_by") or "",
                            "at": data.get("acknowledged_at"),
                            "note": data.get("ack_note") or "",
                        }
                    }
                    if data.get("acknowledged_at")
                    else {}
                ),
            }
        )
    return {
        "active_alert_instance_count": len(down_devices),
        "historical_downtime_seconds": historical_seconds,
        "current_downtime_seconds": current_downtime_seconds,
        # The newest open alert's start: the board's default sort (#228).
        "latest_down_at": latest_down_at,
        "active_cases": sorted(active_cases),
        "down_devices": down_devices,
    }


def read_alert_context(conn, site_id: str, checked_at: str) -> dict:
    """Query one site's alert context on *conn*; raises on database errors."""
    historical_seconds = 0
    open_rows = []
    for data in board_store.site_alert_rows(conn, site_id):
        historical_seconds += int(data.get("total_downtime_seconds") or 0)
        if data.get("status") == "open":
            open_rows.append(data)
    case_numbers_by_instance = board_store.get_case_numbers_for_instances(
        conn,
        [int(data["id"]) for data in open_rows if data.get("id") is not None],
    )
    return summarize_alert_context(historical_seconds, open_rows, case_numbers_by_instance, checked_at)


def get_alert_context_for_site(site_id: str, checked_at: str, conn=None) -> dict:
    owns_conn = conn is None
    if conn is None:
        conn = db.get_conn()
    if conn is None:
        return empty_alert_context()
    try:
        return read_alert_context(conn, site_id, checked_at)
    except Exception as exc:
        logger.debug("Could not load alert context for site %s: %s", site_id, exc)
        return empty_alert_context()
    finally:
        if owns_conn:
            conn.close()


# A "running" sync state older than this is treated as abandoned (e.g. the
# worker that started it was restarted) rather than still in progress.
SYNC_RUNNING_STALE_SECONDS = 900


def nautobot_sync_in_progress() -> bool:
    """Return whether a Nautobot inventory sync is currently running.

    Reads the shared ``inventory_sync_state`` row, so every worker process
    sees syncs started by any other worker.
    """
    if not db.dialect():
        return False
    state = inventory.get_sync_state("nautobot_inventory")
    if state.get("status") != "running":
        return False
    started_at = timeutil.parse_iso_datetime(state.get("last_started_at"))
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - started_at).total_seconds()
    return age_seconds < SYNC_RUNNING_STALE_SECONDS


def inventory_update_schedule() -> tuple[bool, int | None]:
    """Return ``(due, next_update_in_seconds)`` for the configured inventory syncs.

    *due* is true when a sync should start now (by the same rules as
    ``inventory.ensure_snapshot``).  *next_update_in_seconds* is how long
    until the next sync is due: ``0`` when one is due, ``None`` when unknown
    (no persistence, no sources configured, or a sync is running).  The
    board counts down to it (#152).
    """
    sources = []
    if settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN:
        sources.append(("nautobot_inventory", settings.INVENTORY_SYNC_INTERVAL_SECONDS))
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        sources.append(("librenms_inventory", settings.LIBRENMS_SYNC_INTERVAL_SECONDS))
    if not sources or not db.dialect():
        return False, None
    conn = db.get_conn()
    if conn is None:
        return False, None
    try:
        now = datetime.now(UTC)
        due = False
        remaining = []
        for source, interval_seconds in sources:
            state = inventory.get_sync_state(source, conn=conn)
            if inventory.sync_due(source, interval_seconds, conn=conn) or (
                source == "nautobot_inventory" and inventory.cache_version_mismatch(state)
            ):
                due = True
                continue
            completed_at = timeutil.parse_iso_datetime(state.get("last_completed_at"))
            if completed_at is None:
                continue  # running
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            elapsed = (now - completed_at).total_seconds()
            remaining.append(max(0, int(interval_seconds - elapsed)))
        if due:
            return True, 0
        return False, (min(remaining) if remaining else None)
    finally:
        conn.close()


def apply_alert_board_freshness(
    payload: dict,
    sync_enqueued: bool = False,
    next_update_in_seconds: int | None = None,
) -> dict:
    """Attach freshness and sync-progress metadata to an alert-board payload.

    ``sync_pending`` is true while an inventory sync is running (or was just
    enqueued by this request); the UI polls until it clears.
    ``next_update_in_seconds`` is relative so a skewed browser clock does not
    shift the board's countdown.
    """
    checked_at = payload.get("checked_at")
    age_seconds = 0
    if checked_at:
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            age_seconds = max(0, int((datetime.now(UTC) - parsed).total_seconds()))
        except ValueError:
            age_seconds = 0
    result = dict(payload)
    stale_after_seconds = int(result.get("stale_after_seconds", settings.CACHE_TTL))
    result["age_seconds"] = age_seconds
    result["stale"] = age_seconds > stale_after_seconds
    result["sync_pending"] = bool(sync_enqueued) or nautobot_sync_in_progress()
    # The board reads only the persisted snapshot, so without a database it is
    # always empty; the UI uses this flag to say why (#136).
    result["persistence_configured"] = bool(db.dialect())
    result["next_update_in_seconds"] = next_update_in_seconds
    return result


logged_rollup_orphans: set[str] = set()


def roll_up_to_site_locations(
    locations: list[dict],
    devices_by_location: dict[str, list[dict]],
    site_type: str,
    include_non_operational: bool = False,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Group the board by locations of *site_type*, rolling up descendants (#158).

    Returns ``(rows, devices_by_row)``.  Rows are the locations of
    *site_type* (with ``ancestor_path``, e.g. ``"EMEA › DNK"``), plus any other
    location that holds devices but has no ancestor of that type.  Each
    device is assigned to its nearest such ancestor, with ``location_path``
    naming where below the site it sits (``"Bygning A › Etage 2"``).  Unless
    *include_non_operational*, excluded sites are dropped, and so are devices
    below an excluded location.
    """
    by_id = {loc.get("id"): loc for loc in locations if loc.get("id")}

    def is_site(loc: dict) -> bool:
        return (loc.get("location_type") or "").strip().lower() == site_type

    def is_excluded(loc: dict) -> bool:
        return not include_non_operational and location_is_excluded_from_alert_board(loc)

    rows = []
    row_ids = set()
    for loc in locations:
        if is_site(loc) and not is_excluded(loc):
            rows.append({**loc, "ancestor_path": inventory.ancestor_path(loc, by_id)})
            row_ids.add(loc["id"])

    devices_by_row: dict[str, list[dict]] = {}
    for location_id, devices in devices_by_location.items():
        chain = inventory.location_chain(location_id, by_id)
        site_index = next((i for i, loc in enumerate(chain) if is_site(loc)), None)
        if site_index is None:
            # No site above it: the location keeps its own row.
            owner = chain[0] if chain else None
            if owner is None or is_excluded(owner):
                continue
            if owner["id"] not in row_ids:
                rows.append({**owner, "ancestor_path": ""})
                row_ids.add(owner["id"])
                if owner["id"] not in logged_rollup_orphans:
                    logged_rollup_orphans.add(owner["id"])
                    logger.info(
                        "Alert board: location %r has devices but no %r above it; it keeps its own row",
                        owner.get("name"),
                        site_type,
                    )
            below_site = []
            row_id = owner["id"]
        else:
            below_site = chain[:site_index]
            row_id = chain[site_index]["id"]
            if row_id not in row_ids or any(is_excluded(loc) for loc in below_site):
                continue
        location_path = inventory.PATH_SEPARATOR.join(loc.get("name", "") for loc in reversed(below_site))
        devices_by_row.setdefault(row_id, []).extend({**device, "location_path": location_path} for device in devices)
    return rows, devices_by_row


def _fingerprint(items) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]


def site_level_keys(devices: list, down_devices: list, site: dict, override_map: dict) -> tuple[str, str]:
    """A site's ``(criticality, down devices)`` fingerprints this build (#377).

    Criticality: what can be edited: the tier's default, and the devices with
    a level set on them (Nautobot field or override), in maintenance or not.
    A plain device added, removed or put in maintenance doesn't change it.
    """
    edits = [f"tier:{tier_device_level(site.get('tier'))}"]
    for device in devices:
        device_id = device.get("id") or device.get("name") or ""
        if device.get("criticality"):
            edits.append(f"{device_id}:{device['criticality']}")
        elif device_id in override_map:
            edits.append(f"{device_id}:override:{bool(override_map[device_id])}")
    return _fingerprint(edits), _fingerprint(d.get("id") or d.get("name") or "" for d in down_devices)


def record_site_level_changes(
    levels: dict[str, tuple[str, str]],
    checked_at: str,
    sites: dict[str, dict] | None = None,
    quiet_sites: set[str] | None = None,
    level_keys: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Remember each site's alert level and log the changes, for the alert feed (#180).

    *level_keys*: each site's ``(criticality, down devices)`` fingerprints
    (``site_level_keys``).  A change whose criticality changed while its down
    devices did not came from an edit in Nautobot or the settings: logged,
    not notified (#377).

    *levels* maps site id to ``(site_name, alert_level)``.  A site seen for the
    first time only gets its level stored: a new database or a newly added site
    is not a change.  A change that should be notified is queued in the same
    transaction (*sites*: each site's board row, for the message; #282).  The rows are locked in site order, so two builds running
    at once neither deadlock nor log the same change twice.  Returns the number
    of changes logged.
    """
    if not levels:
        return 0
    conn = db.get_conn()
    if conn is None:
        return 0
    changes = 0
    try:
        with db.transaction(conn):
            previous = board_store.lock_site_levels(conn, list(levels))
            for site_id in sorted(levels):
                site_name, level = levels[site_id]
                criticality_key, down_key = (level_keys or {}).get(site_id, (None, None))
                before = previous.get(site_id)
                if before is None:
                    board_store.insert_site_level(
                        conn, site_id, site_name, level, checked_at, criticality_key, down_key
                    )
                    continue
                keys_changed = criticality_key is not None and (
                    (before["criticality_key"], before["down_key"]) != (criticality_key, down_key)
                )
                if before["alert_level"] != level:
                    board_store.record_site_level_change(
                        conn, site_id, site_name, before["alert_level"], level, checked_at
                    )
                    # Only the criticality changed, the same devices down: an edit, not
                    # news (#377).  Unknown before (the first build after the upgrade):
                    # notified as before, so a real outage then still pages.
                    edited = (
                        criticality_key is not None
                        and before["criticality_key"] is not None
                        and before["criticality_key"] != criticality_key
                        and before["down_key"] == down_key
                    )
                    if not edited:
                        notify.enqueue_change(
                            conn,
                            site_id,
                            before["alert_level"],
                            level,
                            (sites or {}).get(site_id),
                            checked_at,
                            quiet_recovery=site_id in (quiet_sites or ()),
                        )
                    changes += 1
                if keys_changed:
                    board_store.set_site_level_keys(conn, site_id, criticality_key, down_key)
    finally:
        conn.close()
    return changes


HISTORY_RETENTION_SOURCE = "alert_history_retention"


def maybe_prune_alert_history(conn) -> dict | None:
    """Prune once a day when ALERT_HISTORY_RETENTION_DAYS is set (scheduler, #194)."""
    days = settings.ALERT_HISTORY_RETENTION_DAYS
    if days <= 0 or not inventory.sync_due(HISTORY_RETENTION_SOURCE, 24 * 3600, conn=conn):
        return None
    started_at = timeutil.iso_utc_now()
    deleted = board_store.prune_alert_history(conn, days)
    with db.transaction(conn):
        inventory.record_sync_state(
            conn,
            HISTORY_RETENTION_SOURCE,
            last_started_at=started_at,
            last_completed_at=timeutil.iso_utc_now(),
            status="idle",
        )
    if any(deleted.values()):
        logger.info(
            "Alert history retention (%d days): deleted %d resolved alerts and %d severity changes",
            days,
            deleted["alert_instances"],
            deleted["site_level_changes"],
        )
    return deleted


def build_alert_board_payload(
    snapshot_only: bool = False,
    include_non_operational: bool = False,
) -> dict:
    """Build and return a fresh alert-board payload."""
    locations = inventory.get_locations(
        include_without_coordinates=True,
        snapshot_only=snapshot_only,
    )
    all_locations = locations
    if not include_non_operational:
        locations = [loc for loc in locations if not location_is_excluded_from_alert_board(loc)]
    alerts = []
    summary = dict.fromkeys(ALERT_LEVEL_ORDER, 0)
    # Site levels this build actually observed, for the alert feed (#180).
    observed_levels: dict[str, tuple[str, str]] = {}
    # Each observed site's row, for the notification of a level change (#282).
    observed_sites: dict[str, dict] = {}
    # Each observed site's (criticality, down devices) fingerprints (#377).
    level_keys: dict[str, tuple[str, str]] = {}
    lnms_devices = None
    lnms_id_map = None
    lnms_index = None
    # Device mappings found by hostname/IP, written once after the build (#240).
    new_librenms_maps: dict = {}
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        try:
            lnms_devices = inventory.read_librenms_devices()
            if not lnms_devices and not snapshot_only:
                inventory.ensure_snapshot(force=True, wait=True)
                lnms_devices = inventory.read_librenms_devices()
            lnms_id_map = board_store.load_librenms_id_map()
        except Exception as exc:
            logger.warning("Could not refresh LibreNMS inventory for alert board: %s", exc)
            lnms_devices = []
            lnms_id_map = {}
        lnms_index = librenms_index(lnms_devices)
    # Alertmanager and webhook alerts (#292), read once for the board.
    webhook_index: dict = {}

    # Read everything that can be read for all sites at once (#149).  A
    # failed connect disables persistence for this build; a failed bulk read
    # falls back to reading per site.
    persistence_unavailable = False
    board_data = None
    read_conn = None
    # Active maintenance windows (#283): whole sites, and single devices.
    in_maintenance = {"sites": {}, "devices": {}, "next_change": None}
    # Couldn't read them: write no history and record no level changes this
    # build, rather than alarm for sites that are in maintenance.
    maintenance_unknown = False
    # Sites whose level dropped only because devices went into maintenance:
    # that drop is planned, not a recovery to notify.
    quiet_sites: set[str] = set()
    # Since when each device is down (#286): read once, written once after the loop.
    # Virtual chassis id -> its members and lead (#402, #405); empty: no stack is named.
    stacks: dict[str, dict] = {}
    device_states = None
    alarmed_device_ids: set[str] = set()
    # What the wireless controllers say (#508, #521); {} without any.
    wireless_index: dict = {}
    # Configured but not readable: the APs they watch would look unmonitored,
    # so, as with maintenance, no history or level changes this build.
    wireless_unknown = False
    # {device_id: (site_id, state row or None)} as this build saw them.
    next_device_states: dict[str, tuple[str, dict | None]] = {}
    try:
        read_conn = db.get_conn()
    except Exception as exc:
        logger.warning("Could not connect to persistence DB for alert board: %s", exc)
    if read_conn is None:
        persistence_unavailable = True
    else:
        try:
            board_data = board_store.read_alert_board_data(read_conn)
        except Exception as exc:
            logger.warning("Could not bulk-read alert board data; reading per site instead: %s", exc, exc_info=True)
        try:
            device_states = board_store.read_device_states(read_conn)
            alarmed_device_ids = board_store.read_alarmed_device_ids(read_conn)
        except Exception as exc:
            device_states = None
            logger.warning("Could not read device states; no alert delay this build: %s", exc)
        try:
            # Every stack's members (#402, #405), wherever they are, once a build.
            stacks = board_stacks(inventory.read_stacks(read_conn))
        except Exception as exc:
            logger.warning("Could not read the stacks; they aren't named this build: %s", exc)
        try:
            webhook_index = webhooks.read_index(read_conn)
        except Exception as exc:
            logger.warning("Could not read webhook device alerts: %s", type(exc).__name__)
        try:
            if wireless.configured():
                wireless_index = wireless.read_index(read_conn)
        except Exception as exc:
            wireless_unknown = True
            logger.warning("Could not read the wireless controllers' access points: %s", exc)
        try:
            in_maintenance = maintenance.read_active(read_conn, maintenance.now())
        except Exception as exc:
            maintenance_unknown = True
            logger.warning(
                "Could not read maintenance windows; no alert history or level changes this build: %s",
                exc,
                exc_info=True,
            )
        finally:
            read_conn.close()

    # One row per site, with the devices of its whole subtree (#158).
    devices_by_row = None
    if settings.ALERT_BOARD_SITE_LOCATION_TYPE:
        if board_data is not None:
            devices_by_location = board_data["devices_by_location"]
        else:
            devices_by_location = {}
            for device in inventory.read_devices():
                devices_by_location.setdefault(device.get("location_id") or "", []).append(device)
        locations, devices_by_row = roll_up_to_site_locations(
            all_locations,
            devices_by_location,
            settings.ALERT_BOARD_SITE_LOCATION_TYPE,
            include_non_operational=include_non_operational,
        )
    else:
        # The full Nautobot location path above each row, as with the roll-up (#178).
        locations = inventory.with_ancestor_paths(locations, all_locations)

    # Writes share one connection, opened on first use.  After a failure it is
    # closed and the next site opens a fresh one, so a broken connection
    # cannot fail every later site (#88).
    write_conn = None
    try:
        for loc in locations:
            site_id = loc.get("id") or ""
            observation_succeeded = True
            bulk_kwargs = {}
            if board_data is not None:
                bulk_kwargs = {
                    # A site missing from the snapshot has no cached devices.
                    "devices_data": board_data["devices_by_location"].get(site_id, [] if snapshot_only else None),
                    "devices_already_normalized": True,
                    "override_map": board_data["override_map"],
                }
            if devices_by_row is not None:
                bulk_kwargs["devices_data"] = devices_by_row.get(site_id, [])
                bulk_kwargs["devices_already_normalized"] = True
            try:
                devices, alert = get_location_devices_and_alert(
                    loc["id"],
                    loc.get("location_type") or None,
                    lnms_devices=lnms_devices,
                    lnms_id_map=lnms_id_map,
                    lnms_index=lnms_index,
                    new_librenms_maps=new_librenms_maps,
                    webhook_index=webhook_index,
                    wireless_index=wireless_index,
                    snapshot_only=snapshot_only,
                    tier=loc.get("tier") or "",
                    **board_device_rules(),
                    **bulk_kwargs,
                )
            except Exception as exc:
                logger.warning(
                    "Could not compute alert summary for location %s: %s",
                    loc.get("id"),
                    exc,
                )
                observation_succeeded = False
                devices = []
                alert = {"level": "no_data", "reason": "Could not compute alert state"}

            # Devices in maintenance are left out of the site's level; their
            # open alerts stay as they are (#283).
            maintenance_devices = [d for d in devices if (d.get("id") or "") in in_maintenance["devices"]]
            if maintenance_devices:
                level_with_them = (alert.get("level") or "ok").lower()
                devices = [d for d in devices if (d.get("id") or "") not in in_maintenance["devices"]]
                alert = compute_alert_level(
                    devices,
                    loc.get("location_type") or None,
                    override_map=bulk_kwargs.get("override_map"),
                    tier=loc.get("tier") or "",
                )
                if notify.LEVEL_RANK.get(level_with_them, 0) > notify.LEVEL_RANK.get(alert.get("level"), 0):
                    quiet_sites.add(site_id)
            # Frozen: every device of this site with an active window, also one
            # no longer in the inventory (its open alert must not resolve).
            frozen_device_ids = {d.get("id") for d in maintenance_devices} | {
                device_id for device_id, window in in_maintenance["devices"].items() if window["site_id"] == site_id
            }
            site_window = in_maintenance["sites"].get(site_id)
            checked_at = timeutil.iso_utc_now()
            # Since when each device is down, also before it alarms, and
            # whether it is flapping (#286).
            flapping_ended: dict[str, str] = {}  # device_id: when it came back up
            recompute = False
            if observation_succeeded and device_states is not None:
                in_window = {d.get("id") for d in maintenance_devices}
                updated = []
                for device in devices + maintenance_devices:
                    device_id = device.get("id") or ""
                    if not device_id:
                        updated.append(device)
                        continue
                    stored = device_states.get(device_id)
                    is_down = (device.get("status") or "").lower().strip() in DOWN_STATUSES
                    if is_down:
                        since = earliest_time(
                            down_since(device, checked_at),
                            stored["since"] if stored and stored["state"] == "down" else None,
                        )
                    else:
                        since = came_up_at(device, stored, checked_at)
                    row = next_device_state(
                        stored, is_down, since, checked_at, source=device.get("down_source") or "nautobot"
                    )
                    next_device_states[device_id] = (site_id, row)
                    device = dict(device)  # never change the (cached) inventory's dicts
                    if is_down:
                        device["first_seen_down_at"] = row["since"]
                    if row and row["flapping_since"] and device_id not in in_window:
                        # One incident, a steady level: it counts as down while up.
                        device["flapping"] = True
                        device["flap_changes"] = len(row["changes"])
                        if not is_down:
                            device["status"] = "Flapping"
                            # Down since its first change in the window, not since
                            # Nautobot's last edit: the status is ours.
                            device["down_source"] = "flapping"
                            device["first_seen_down_at"] = (row["changes"] or [row["flapping_since"]])[0]
                            recompute = True
                    elif stored and stored.get("flapping_since") and not is_down:
                        # Resolved when it came back up, not when the steady window ended.
                        flapping_ended[device_id] = stored["since"] if stored["state"] == "up" else since
                    updated.append(device)
                devices = [d for d in updated if d.get("id") not in in_window or not d.get("id")]
                maintenance_devices = [d for d in updated if d.get("id") and d.get("id") in in_window]
            # Down for less than ALERT_DELAY_SECONDS and not alarmed yet: not
            # shown, counted or recorded; if it comes back, it never happened.
            pending_devices = []
            if settings.ALERT_DELAY_SECONDS and device_states is not None:
                pending_devices = [
                    d
                    for d in devices
                    if (d.get("status") or "").lower().strip() in DOWN_STATUSES
                    and d.get("first_seen_down_at")
                    and not d.get("flapping")
                    and (d.get("id") or "") not in alarmed_device_ids
                    and seconds_between(d["first_seen_down_at"], checked_at) < settings.ALERT_DELAY_SECONDS
                ]
                if pending_devices:
                    devices = [d for d in devices if d not in pending_devices]
                    recompute = True
            if recompute:
                alert = compute_alert_level(
                    devices,
                    loc.get("location_type") or None,
                    override_map=bulk_kwargs.get("override_map"),
                    tier=loc.get("tier") or "",
                )
                if not devices and pending_devices:
                    # Only devices still within the delay: monitored, and OK so
                    # far; not "no data" (#316).
                    alert = {"level": "ok", "reason": ""}
            down_devices = [d for d in devices if (d.get("status") or "").lower().strip() in DOWN_STATUSES]
            # The likely cause (#285): with a core device down, the others
            # down at the site are probably behind it.
            cores = (
                down_core_devices(devices, loc.get("location_type") or None, bulk_kwargs.get("override_map"))
                if down_devices
                else []
            )
            core_ids = {d.get("id") for d in cores if d.get("id")}
            core_names = ", ".join(d.get("name") or d.get("id") or "" for d in cores)
            probable_causes = {
                d.get("id"): core_names for d in down_devices if cores and d.get("id") and d.get("id") not in core_ids
            }
            alert_context = empty_alert_context()
            if board_data is not None:
                primary_ip_backfill_pending = board_data["primary_ip_backfill_pending"]
                # Otherwise the lifecycle step has nothing to open, update or resolve.
                needs_write = (
                    observation_succeeded
                    and not primary_ip_backfill_pending
                    and (bool(down_devices) or site_id in board_data["open_rows_by_site"])
                )
            else:
                primary_ip_backfill_pending = None  # read per site below
                needs_write = True
            # A site in maintenance: its history is frozen (read, not written).
            # Windows or controllers that couldn't be read: nothing is written either.
            if site_window is not None or maintenance_unknown or wireless_unknown:
                needs_write = False

            wrote = False
            if needs_write and not persistence_unavailable:
                if write_conn is None:
                    try:
                        write_conn = db.get_conn()
                    except Exception as exc:
                        logger.warning("Could not connect to persistence DB for alert board: %s", exc)
                    if write_conn is None:
                        persistence_unavailable = True
                if write_conn is not None:
                    try:
                        if primary_ip_backfill_pending is None:
                            primary_ip_backfill_pending = board_store.nautobot_inventory_primary_ip_backfill_pending(
                                conn=write_conn
                            )
                        if observation_succeeded and not primary_ip_backfill_pending:
                            if (
                                upsert_alert_lifecycle_for_site(
                                    loc,
                                    devices,
                                    alert,
                                    checked_at,
                                    conn=write_conn,
                                    frozen_device_ids=frozen_device_ids,
                                    resolved_at_by_device=flapping_ended,
                                    probable_causes=probable_causes,
                                    core_device_ids=core_ids,
                                )
                                is False
                            ):
                                raise RuntimeError("alert lifecycle write failed")
                        alert_context = read_alert_context(write_conn, site_id, checked_at)
                        wrote = True
                    except Exception as exc:
                        logger.warning(
                            "Alert board persistence failed for site %s; the next site reconnects: %s",
                            site_id,
                            exc,
                        )
                        write_conn.close()
                        write_conn = None
            if not wrote and board_data is not None:
                alert_context = summarize_alert_context(
                    board_data["historical_downtime_by_site"].get(site_id, 0),
                    board_data["open_rows_by_site"].get(site_id, []),
                    board_data["case_numbers_by_instance"],
                    checked_at,
                )
            if primary_ip_backfill_pending:
                alert_context = {
                    **alert_context,
                    "active_alert_instance_count": 0,
                    "current_downtime_seconds": 0,
                    "latest_down_at": None,
                    "active_cases": [],
                    "down_devices": [],
                }
            device_ip_by_key = {
                (device.get("id") or device.get("name") or ""): device_display_ip(device)
                for device in devices + maintenance_devices
            }
            maintenance_ids = {d.get("id") for d in maintenance_devices}
            current_down_devices = [
                {
                    "device_id": device.get("id") or "",
                    "device_name": device.get("name") or "Unknown",
                    "device_ip": device_display_ip(device),
                    "status": device.get("status") or "",
                    "role": device.get("role") or "",
                    "case_numbers": [],
                    # Only with ALERT_BOARD_SITE_LOCATION_TYPE, for devices below the site (#158).
                    **({"location_path": device["location_path"]} if device.get("location_path") else {}),
                    # Up and down FLAP_CHANGES times or more within 30 minutes (#286).
                    **({"flapping": True, "flap_changes": device["flap_changes"]} if device.get("flapping") else {}),
                    # Down per Alertmanager or a webhook, not Nautobot (#292); per a
                    # wireless controller, or as it last said (#521).
                    **({"down_reason": webhook_reason(device)} if device.get("webhook_since") else {}),
                    **(
                        {"down_reason": wireless.reason(device["wireless"])}
                        if device.get("down_source") == "wireless"
                        else {}
                    ),
                    # Watched by a wireless controller (#521): folded under the others.
                    **({"access_point": True} if device.get("wireless") else {}),
                    # Part of a stack: which, and its place in it (#400).
                    # In maintenance it isn't counted: not "all members down" (#405).
                    **(
                        {"stack": stack}
                        if (stack := stack_of(device, stacks, device.get("id") not in maintenance_ids))
                        else {}
                    ),
                }
                # Down devices in maintenance are listed (marked below) but not counted (#283).
                for device in down_devices
                + [d for d in maintenance_devices if (d.get("status") or "").lower().strip() in DOWN_STATUSES]
            ]
            current_down_device_map = {item["device_id"] or item["device_name"]: item for item in current_down_devices}
            # Details of every device in maintenance, also one back up whose
            # alert is kept open (frozen), so its row isn't blank (#283).
            maintenance_details = {
                (device.get("id") or ""): {
                    "device_id": device.get("id") or "",
                    "device_name": device.get("name") or "Unknown",
                    "status": device.get("status") or "",
                    "role": device.get("role") or "",
                    **({"location_path": device["location_path"]} if device.get("location_path") else {}),
                    **({"stack": stack} if (stack := stack_of(device, stacks, counted=False)) else {}),
                }
                for device in maintenance_devices
            }
            merged_down_devices = []
            seen_down_device_keys: set[str] = set()
            for item in alert_context["down_devices"]:
                item_key = item.get("device_id") or item.get("device_name") or ""
                merged = dict(item)
                current_item = current_down_device_map.get(item_key) or maintenance_details.get(item_key, {})
                merged.update(
                    {
                        "device_id": current_item.get("device_id", merged.get("device_id", "")),
                        "device_name": current_item.get("device_name", merged.get("device_name", "")),
                        "status": current_item.get("status", merged.get("status", "")),
                        "role": current_item.get("role", merged.get("role", "")),
                    }
                )
                if current_item.get("location_path"):
                    merged["location_path"] = current_item["location_path"]
                if current_item.get("flapping"):
                    merged["flapping"] = True
                    merged["flap_changes"] = current_item["flap_changes"]
                if current_item.get("down_reason"):
                    merged["down_reason"] = current_item["down_reason"]
                if current_item.get("access_point"):
                    merged["access_point"] = True
                if current_item.get("stack"):
                    merged["stack"] = current_item["stack"]
                merged["device_ip"] = device_ip_by_key.get(item_key, "")
                merged.setdefault("status", "")
                merged.setdefault("role", "")
                merged.setdefault("case_numbers", [])
                merged_down_devices.append(merged)
                if item_key:
                    seen_down_device_keys.add(item_key)
            for item in current_down_devices:
                item_key = item["device_id"] or item["device_name"]
                if item_key in seen_down_device_keys:
                    continue
                merged_down_devices.append(item)
                if item_key:
                    seen_down_device_keys.add(item_key)
            # A level set on the device in Nautobot, marked on the board (#377).
            own_levels = {d.get("id"): d["criticality"] for d in devices if d.get("id") and d.get("criticality")}
            for item in merged_down_devices:
                if item.get("device_id") in own_levels:
                    item["set_level"] = own_levels[item["device_id"]]
                stored_cause = item.pop("stored_probable_cause", "")
                stored_core = item.pop("stored_core", False)
                window = in_maintenance["devices"].get(item.get("device_id") or "")
                if window is not None:
                    item["maintenance_until"] = window["ends_at"]
                    item["maintenance_reason"] = window["reason"]
                elif not observation_succeeded:
                    # Nothing observed this build: keep what the history says.
                    if stored_core:
                        item["core"] = True
                    elif stored_cause:
                        item["probable_cause"] = stored_cause
                elif item.get("device_id") in core_ids:
                    item["core"] = True
                elif item.get("device_id") in probable_causes:
                    item["probable_cause"] = probable_causes[item["device_id"]]
            if not observation_succeeded:
                # The cores are the devices the others are likely caused by.
                named = {
                    name.strip()
                    for item in merged_down_devices
                    for name in (item.get("probable_cause") or "").split(",")
                    if name.strip()
                }
                for item in merged_down_devices:
                    if item.get("device_name") in named and not item.get("maintenance_until"):
                        item["core"] = True
            # The cause first, then what is behind it (#285).
            merged_down_devices.sort(key=lambda item: not item.get("core"))
            level = (alert.get("level") or "ok").lower()
            if site_window is not None:
                level = "maintenance"
            # Only access points down (#521): the Wireless chip, not an alarm;
            # its level shows on the map, but the feed and notifications see OK.
            wireless_only = bool(alert.get("wireless_only")) and level != "maintenance"
            key = shown_level(level, wireless_only)
            summary[key] = summary.get(key, 0) + 1
            if observation_succeeded and site_id:
                observed_levels[site_id] = (loc.get("name") or "", "ok" if wireless_only else level)
                level_keys[site_id] = site_level_keys(
                    [*devices, *maintenance_devices, *pending_devices],
                    down_devices,
                    loc,
                    bulk_kwargs.get("override_map") or {},
                )
            tenants = loc.get("tenants") or inventory.site_tenants(loc.get("tenant") or "", [])
            tenant_scopes = loc.get("tenant_scopes") or {}
            tenant_alerts = tenant_scoped_alerts(
                tenant_scopes,
                tenants,
                devices,
                pending_devices,
                maintenance_devices,
                site_window,
                checked_at,
                loc.get("location_type") or None,
                bulk_kwargs.get("override_map") or {},
                loc.get("tier") or "",
            )
            # "12 network · 40 APs" (#499), those in maintenance too; down
            # (#503) only the counted ones: none in a device or site window
            # (a site window leaves its devices counted, for the level).
            kinds = device_kinds(
                [*devices, *pending_devices, *maintenance_devices],
                [] if site_window is not None else down_devices,
            )
            alerts.append(
                {
                    **{key: value for key, value in loc.items() if key != "tenant_scopes"},
                    # Its own tenant first, then those linked by a Relationship (#238).
                    "tenants": tenants,
                    "tenant_alerts": tenant_alerts,
                    "alert_level": level,
                    "alert_reason": alert.get("reason", ""),
                    **({"wireless_only": True} if wireless_only else {}),
                    # Importance (#293): a known tier and its rank (0 = most important).
                    "tier": (loc.get("tier") or "") if tier_rank(loc.get("tier")) < 99 else "",
                    "tier_rank": tier_rank(loc.get("tier")),
                    "device_count": len(devices) + len(pending_devices),
                    # Left out of device_count while in a window (#283).
                    "maintenance_device_count": len(maintenance_devices),
                    "down_device_count": len(down_devices),
                    **kinds,
                    "current_downtime_seconds": alert_context["current_downtime_seconds"],
                    "latest_down_at": alert_context.get("latest_down_at"),
                    "historical_downtime_seconds": alert_context["historical_downtime_seconds"],
                    "active_alert_instance_count": max(
                        int(alert_context["active_alert_instance_count"]),
                        len(merged_down_devices),
                    ),
                    "active_cases": alert_context["active_cases"],
                    "down_devices": merged_down_devices,
                    **(
                        {
                            "maintenance": {
                                "until": site_window["ends_at"],
                                "reason": site_window["reason"],
                                "by": site_window["created_by"],
                            }
                        }
                        if site_window is not None
                        else {}
                    ),
                }
            )
            if observation_succeeded and site_id:
                observed_sites[site_id] = alerts[-1]
        # Every device state this build saw, in one write, only when changed (#286).
        now = timeutil.iso_utc_now()
        if (
            device_states is not None
            and not persistence_unavailable
            and device_states_to_write(next_device_states, device_states, now)
        ):
            try:
                if write_conn is None:
                    write_conn = db.get_conn()
                if write_conn is not None:
                    write_device_states(write_conn, next_device_states, device_states, now)
            except Exception as exc:
                logger.warning("Could not store device states: %s", exc, exc_info=True)
    finally:
        if write_conn is not None:
            write_conn.close()

    board_store.store_librenms_maps(new_librenms_maps)

    if not persistence_unavailable and not maintenance_unknown and not wireless_unknown:
        try:
            record_site_level_changes(observed_levels, timeutil.iso_utc_now(), observed_sites, quiet_sites, level_keys)
        except Exception as exc:
            logger.warning("Could not record site alert level changes: %s", exc, exc_info=True)

    alerts.sort(
        key=lambda item: (
            alert_sort_key(shown_level(item.get("alert_level", "ok"), bool(item.get("wireless_only")))),
            item.get("tier_rank", 99),  # more important sites first within a level (#293)
            -item.get("down_device_count", 0),
            item.get("name", "").lower(),
        )
    )

    return {
        "checked_at": timeutil.iso_utc_now(),
        "stale_after_seconds": settings.CACHE_TTL,
        "summary": {
            "total": len(alerts),
            **{level: summary.get(level, 0) for level in ALERT_LEVEL_ORDER},
            # Planned work (#283); not an alarm, not in non_ok.
            "maintenance": summary.get("maintenance", 0),
            # No data is not an alert (#124).
            "non_ok": sum(summary.get(level, 0) for level in ALERT_LEVELS_NON_OK),
        },
        "alerts": alerts,
        # When the next maintenance window starts or ends: the cached board
        # expires then at the latest (#283).
        "next_maintenance_change": in_maintenance["next_change"],
    }


def site_monitored_devices(site_id: str, include_non_operational: bool = False) -> list[dict]:
    """The devices the board counts for the row *site_id*, for choosing what
    goes into maintenance (#283): with a primary IP or a wireless controller
    (#521), not of an excluded status, including devices below the site when rows are rolled up (not
    those below an excluded location, unless *include_non_operational*, as
    on the board)."""
    devices = inventory.read_devices()
    if settings.ALERT_BOARD_SITE_LOCATION_TYPE:
        devices_by_location: dict[str, list] = {}
        for device in devices:
            devices_by_location.setdefault(device.get("location_id") or "", []).append(device)
        locations = inventory.get_locations(include_without_coordinates=True, snapshot_only=True)
        _, devices_by_row = roll_up_to_site_locations(
            locations,
            devices_by_location,
            settings.ALERT_BOARD_SITE_LOCATION_TYPE,
            include_non_operational=include_non_operational,
        )
        devices = devices_by_row.get(site_id, [])
    else:
        devices = [device for device in devices if (device.get("location_id") or "") == site_id]
    monitored = merge_stacks(
        [
            device
            for device in wireless.attach(devices, wireless.load_index(devices))
            if is_monitored(device)
            and not status_is_excluded(device.get("status"), settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES)
        ]
    )
    return sorted(
        (
            {
                "id": device.get("id") or "",
                "name": device.get("name") or "",
                "role": device.get("role") or "",
                "status": device.get("status") or "",
                "location_path": device.get("location_path") or "",
            }
            for device in monitored
        ),
        key=lambda device: device["name"].lower(),
    )


BOARD_CACHE_KEY = "alert-board-data:v3"
# The stored board outlives the sync interval a little, so it is replaced by
# the scheduler's next build before it expires (#295).
BOARD_TTL_SLACK_SECONDS = 90
BOARD_BUILD_LOCK = "alert_board_build"
BOARD_BUILD_WAIT_SECONDS = 60


def board_cache_seconds(payload: dict) -> int:
    """How long a built board is stored: until the next maintenance change at the latest."""
    return maintenance.cache_seconds(
        payload.get("next_maintenance_change"), settings.CACHE_TTL + BOARD_TTL_SLACK_SECONDS
    )


def _board_build_lock(stored=None):
    """The build lock (a release function), or None: no database, or still
    held by another build after BOARD_BUILD_WAIT_SECONDS (then build anyway).

    While waiting, *stored* (a function) is asked for the board another
    build stored; once there, that is returned as ``("stored", board)``.
    """
    deadline = time.monotonic() + BOARD_BUILD_WAIT_SECONDS
    while True:
        try:
            release = db.try_advisory_lock(BOARD_BUILD_LOCK)
        except Exception as exc:
            logger.warning("Alert board build lock unavailable: %s", type(exc).__name__)
            return None
        if release is None or callable(release):
            return release
        if time.monotonic() >= deadline:
            logger.warning("Alert board: another build took over %ss; building anyway", BOARD_BUILD_WAIT_SECONDS)
            return None
        time.sleep(0.1)
        found = stored() if stored is not None else None
        if found is not None:
            return ("stored", found)


def board(trigger: str = "request", rebuild: bool = False, include_non_operational: bool = False) -> dict:
    """The stored alert board, built when it is missing, or with *rebuild*.

    One build at a time across workers (#295): a caller that finds another
    build running waits for it and takes its result instead of building the
    same board again.  *trigger* (``request``, ``scheduler``, ``push``,
    ``change``) is counted in board_build_stats.  *include_non_operational*:
    the variant with the excluded locations, stored on its own.
    """
    key = f"{BOARD_CACHE_KEY}:include-non-operational" if include_non_operational else BOARD_CACHE_KEY
    if not rebuild:
        cached = caching.get(key)
        if cached is not None:
            return cached
    release = _board_build_lock(None if rebuild else lambda: caching.get(key))
    if isinstance(release, tuple):
        return release[1]  # another worker built it while we waited
    try:
        if not rebuild:
            cached = caching.get(key)
            if cached is not None:
                return cached  # built while we waited
        started = time.monotonic()
        payload = (
            build_alert_board_payload(snapshot_only=True, include_non_operational=True)
            if include_non_operational
            else build_alert_board_payload(snapshot_only=True)
        )
        if payload.get("alerts") or inventory.snapshot_initialized():
            caching.set(key, payload, timeout=board_cache_seconds(payload))
        board_store.record_board_build(trigger, time.monotonic() - started)
        return payload
    finally:
        if callable(release):
            release()


def get_alert_board_data(
    force_refresh: bool = False,
    include_non_operational: bool = False,
) -> dict:
    """Return alert summaries for all locations."""
    sync_enqueued = False
    if force_refresh:
        # "Sync now": incremental, not a full reconcile (#135).  Deletions are
        # still caught by the scheduled full reconcile.
        sync_enqueued = inventory.ensure_snapshot(
            force=True, full=False, wait=False, cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS
        )
    sync_due, next_update_in_seconds = inventory_update_schedule()
    if not sync_enqueued and sync_due:
        # A sync is due (or none has run yet): start it in the background, so
        # an open board keeps itself up to date (#152).  This request still
        # makes no upstream calls itself.
        sync_enqueued = inventory.ensure_snapshot(wait=False)
    return apply_alert_board_freshness(
        board("request", include_non_operational=include_non_operational),
        sync_enqueued=sync_enqueued,
        next_update_in_seconds=next_update_in_seconds,
    )


LOCATION_ALERTS_CACHE_KEY = "location-alert-levels:v1"


def build_location_alert_levels() -> dict:
    """Score every mapped location from the inventory snapshot (#234).

    The same scoring as the map's site panel (``get_location_detail``): each
    location's own devices, criticality overrides and the LibreNMS status,
    read for all locations at once.  Makes no upstream calls.  Locations
    with monitored devices are returned (critical, medium, low or ok; #311:
    the map tells OK from no data), and sites in maintenance.
    """
    locations = inventory.get_locations(snapshot_only=True)
    devices_by_location: dict[str, list] = {}
    override_map: dict = {}
    in_maintenance = {"sites": {}, "devices": {}, "next_change": None}
    # Locations with devices held back by the alert delay: they have
    # monitored devices, so they are OK even with no others (#311).
    held_back: dict[str, int] = {}
    webhook_index: dict = {}
    wireless_index: dict = {}
    conn = db.get_conn()
    if conn is not None:
        try:
            try:
                wireless_index = wireless.read_index(conn) if wireless.configured() else {}
            except Exception as exc:
                logger.warning("Could not read the wireless controllers' access points for the map: %s", exc)
            # Devices in maintenance don't count, like on the board (#283),
            # nor ones down for less than the alert delay (#286).
            in_maintenance = maintenance.read_active(conn, maintenance.now())
            pending = board_store.pending_device_ids(conn, timeutil.iso_utc_now())
            flapping = board_store.flapping_device_ids(conn, timeutil.iso_utc_now(), FLAP_WINDOW)
            for device in wireless.attach(inventory.read_devices(conn=conn), wireless_index):
                if (device.get("id") or "") in pending:
                    # Only a device the board counts (its state row can outlive its IP).
                    if not not_monitored_reason(device):
                        location = device.get("location_id") or ""
                        held_back[location] = held_back.get(location, 0) + 1
                    continue
                if (device.get("id") or "") in in_maintenance["devices"]:
                    continue
                if (device.get("id") or "") in flapping:
                    device = {**device, "status": "Flapping"}  # counts as down, like on the board (#286)
                devices_by_location.setdefault(device.get("location_id") or "", []).append(device)
            override_map = board_store.read_criticality_overrides(conn)
            webhook_index = webhooks.read_index(conn)
        finally:
            conn.close()
    lnms_devices = None
    lnms_id_map = None
    lnms_index = None
    new_librenms_maps: dict = {}
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        lnms_devices = inventory.read_librenms_devices()
        lnms_id_map = board_store.load_librenms_id_map()
        lnms_index = librenms_index(lnms_devices)

    # The board's rows are the truth (#316): a site has the same level, the
    # same "N down · M monitored", on the board, on the map and in its panel.
    board_rows: dict[str, dict] = {}
    try:
        board_rows = {str(item.get("id")): item for item in cached_alert_board().get("alerts") or [] if item.get("id")}
    except Exception as exc:
        logger.warning("Could not read the alert board for the map; scoring locations alone: %s", exc)

    levels = {}
    for loc in locations:
        location_id = loc.get("id") or ""
        row = board_rows.get(location_id)
        if row is not None:
            # Every level the row has, "no data" and maintenance too: the panel
            # must not score a row itself (#316).
            level = (row.get("alert_level") or "no_data").lower()
            window = row.get("maintenance") or {}
            levels[location_id] = {
                "level": level,
                "reason": window.get("reason", "") if level == "maintenance" else row.get("alert_reason", ""),
                "down": row.get("down_device_count", 0),
                "monitored": row.get("device_count", 0),
                # Only access points down (#521): its colour, not in the map's alarms.
                **({"wireless_only": True} if row.get("wireless_only") else {}),
                # When its newest open alert started: the map's alarm list puts the newest first (#334).
                **({"since": row["latest_down_at"]} if row.get("latest_down_at") else {}),
                **({"until": window["until"]} if level == "maintenance" and window.get("until") else {}),
            }
            continue
        # Not a board row (below a rolled-up site, or a location the board
        # hides): its own devices, by the board's rule.
        devices = devices_by_location.get(location_id)
        if not location_id or not devices:
            continue
        counted, alert = get_location_devices_and_alert(
            location_id,
            loc.get("location_type") or None,
            devices_data=devices,
            devices_already_normalized=True,
            lnms_devices=lnms_devices,
            lnms_id_map=lnms_id_map,
            lnms_index=lnms_index,
            new_librenms_maps=new_librenms_maps,
            webhook_index=webhook_index,
            wireless_index=wireless_index,
            snapshot_only=True,
            override_map=override_map,
            tier=loc.get("tier") or "",
            **board_device_rules(),
        )
        if alert["level"] in ALERT_LEVELS_NON_OK or alert["level"] == "ok":
            levels[location_id] = {
                "level": alert["level"],
                "reason": alert.get("reason", ""),
                **({"wireless_only": True} if alert.get("wireless_only") else {}),
                "down": sum((d.get("status") or "").lower().strip() in DOWN_STATUSES for d in counted),
                # Devices within the alert delay are monitored, as on the board.
                "monitored": len(counted) + held_back.get(location_id, 0),
            }
    board_store.store_librenms_maps(new_librenms_maps)
    for location_id, count in held_back.items():
        if location_id and location_id not in levels and location_id not in board_rows:
            levels[location_id] = {"level": "ok", "reason": "", "down": 0, "monitored": count}
    # A site in maintenance shows that instead of its level (#283).
    for site_id, window in in_maintenance["sites"].items():
        levels[site_id] = {
            **levels.get(site_id, {}),  # the counts stay
            "level": "maintenance",
            "reason": window["reason"],
            "until": window["ends_at"],
        }
    return {
        "checked_at": timeutil.iso_utc_now(),
        "levels": levels,
        "next_maintenance_change": in_maintenance["next_change"],
    }


def cached_alert_board() -> dict:
    """The alert board as cached, built and cached when it isn't; starts no sync."""
    return board("request")


def get_location_alert_levels() -> dict:
    """``build_location_alert_levels``, cached like the alert board (``CACHE_TTL``)."""
    cached = caching.get(LOCATION_ALERTS_CACHE_KEY)
    if cached is not None:
        return cached
    payload = build_location_alert_levels()
    if payload["levels"] or inventory.snapshot_initialized():
        caching.set(
            LOCATION_ALERTS_CACHE_KEY,
            payload,
            timeout=maintenance.cache_seconds(payload.get("next_maintenance_change"), settings.CACHE_TTL),
        )
    return payload


def location_field_asns(location_id: str) -> list:
    """Return the ASN stored directly on a location as an ASN-list entry.

    Nautobot 3.x core keeps a single integer ``asn`` field on each Location
    instead of the ``ipam/asns/`` endpoint.  The cached inventory is preferred;
    otherwise the location is looked up once.  Upstream errors propagate.
    """
    asn = None
    cached = [loc for loc in inventory.read_locations(include_without_coordinates=True) if loc.get("id") == location_id]
    if cached:
        asn = cached[0].get("asn")
    else:
        asn = nautobot.get(f"dcim/locations/{location_id}/").get("asn")
    if asn in (None, ""):
        return []
    return [{"asn": asn, "description": "", "tenant": ""}]


def get_location_detail(location_id: str, location_type: str | None = None) -> dict:
    """Fetch detailed info (devices, ASNs, circuits) for a single location.

    Upstream failures raise so the caller can report them.  The one exception
    is a 404 from ``ipam/asns/``: that endpoint does not exist on Nautobot 3.x
    without the BGP Models plugin, so the location's own ``asn`` field is used.
    """
    devices, _ = get_location_devices_and_alert(location_id, location_type)
    # The level the board and the map show (#316); without a database (map
    # only), the same rule on the devices read now.
    in_window: dict = {}
    pending: set = set()
    overrides: dict = {}
    # Who is on it (#512); None: not read (no database, or it failed), so the
    # panel doesn't say "No case" for what it doesn't know.
    cases: dict | None = None
    conn = db.get_conn() if db.dialect() else None
    if conn is not None:
        try:
            try:
                in_window = maintenance.read_active(conn, maintenance.now())["devices"]
                pending = board_store.pending_device_ids(conn, timeutil.iso_utc_now())
            except Exception as exc:
                logger.warning("Could not read maintenance and alert delay for the site panel: %s", exc)
            ids = [d.get("id") for d in devices if d.get("id")]
            try:
                overrides = board_store.read_criticality_overrides(conn, ids) if ids else {}
            except Exception as exc:
                logger.warning("Could not read criticality overrides for the site panel: %s", exc)
            try:
                cases = board_store.open_cases_for_devices(conn, ids)
            except Exception as exc:
                logger.warning("Could not read the cases for the site panel: %s", exc)
        finally:
            conn.close()
    # The panel's down list in the board's order (#394): each device's level as
    # the board weighs it (#377), and whether it is core, the likely cause (#285).
    core_keywords = get_critical_keywords(location_type)
    for device in devices:
        device["core"] = is_core_device(device, core_keywords, overrides)
        device["level"] = device_level(device, core_keywords, overrides)[0]
        device["not_monitored"] = not_monitored_reason(device)
        # Monitored, but left out of the counts for now: say why (#316).
        window = in_window.get(device.get("id") or "")
        if window is not None and not device["not_monitored"]:
            device["maintenance_until"] = window["ends_at"]
        if (device.get("id") or "") in pending and not device["not_monitored"]:
            device["alert_pending"] = True
        on_it = (cases or {}).get("devices", {}).get(device.get("id") or "")
        if on_it:
            device["case_numbers"] = on_it["case_numbers"]
            device["acknowledged_by"] = on_it["acknowledged_by"]
    # A stack is one device (#405): its lead is down when any of its IPs is,
    # and its other members are monitored with it, not on their own.
    for lead, others, down in stack_groups([d for d in devices if not d["not_monitored"]]):
        if down is not None and down is not lead:
            lead.update({key: down[key] for key in STACK_DOWN_FIELDS if key in down})
        for member in others:
            member["not_monitored"] = "monitored with its stack"
            member.pop("maintenance_until", None)
            member.pop("alert_pending", None)
            # A member's open alert is the stack's (#512): its case on the lead's row.
            member_cases = member.pop("case_numbers", None)
            member_ack = member.pop("acknowledged_by", None)
            if member_cases is not None:
                lead["case_numbers"] = list(dict.fromkeys([*(lead.get("case_numbers") or []), *member_cases]))
                lead["acknowledged_by"] = lead.get("acknowledged_by") or member_ack or ""
    level = get_location_alert_levels()["levels"].get(location_id) if db.dialect() else None
    if level is not None:
        alert = {
            "level": level["level"],
            "reason": level.get("reason", ""),
            **({"wireless_only": True} if level.get("wireless_only") else {}),
        }
        counts = {key: level[key] for key in ("down", "monitored") if key in level}
    else:
        monitored = [device for device in devices if not device["not_monitored"]]
        alert = compute_alert_level(monitored, location_type)
        counts = {
            "down": sum((d.get("status") or "").lower().strip() in DOWN_STATUSES for d in monitored),
            "monitored": len(monitored),
        }
    try:
        asns_data = nautobot.fetch_all_pages("ipam/asns/", {"location_id": location_id})
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        asns = location_field_asns(location_id)
    else:
        asns = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": nautobot.nested_str(a.get("tenant"), "name", "display"),
            }
            for a in asns_data
        ]
    circuits, circuits_error = location_circuits(location_id)
    if not circuits_error:  # without the terminations, they couldn't be told apart (#383)
        circuits += address_circuits(location_id, {circuit["id"] for circuit in circuits})
    return {
        "devices": devices,
        "alert": alert,
        "counts": counts,
        # The site's cases (#512): every open one of its devices, newest first, once.
        "cases": (cases or {}).get("cases", []),
        "cases_read": cases is not None,
        "asns": asns,
        "circuits": circuits,
        "circuits_error": circuits_error,
    }


def location_circuits(location_id: str) -> tuple[list[dict], str]:
    """Return ``(circuits, error)``: every circuit termination at *location_id* (#235).

    Circuits are optional: a failed call gives an empty list and an error
    message, so the rest of the site panel still loads.  A 404 means Nautobot
    has no circuits app; that is not an error.
    """
    try:
        terminations = nautobot.fetch_all_pages("circuits/circuit-terminations/", {"location": location_id, "depth": 2})
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return [], ""
        logger.warning("Could not fetch circuits for location %s: %s", location_id, exc)
        return [], "Circuit information unavailable"
    except Exception as exc:
        logger.warning("Could not fetch circuits for location %s: %s", location_id, exc)
        return [], "Circuit information unavailable"

    circuits = []
    for term in terminations:
        circuit = term.get("circuit") if isinstance(term.get("circuit"), dict) else {}
        circuits.append(
            {
                "id": circuit.get("id") or "",
                "cid": inventory.circuit_cid({"cid": circuit.get("cid") or nautobot.nested_str(circuit, "display")}),
                "provider": nautobot.nested_str(circuit.get("provider"), "name", "display"),
                "circuit_type": nautobot.nested_str(circuit.get("circuit_type"), "name", "display"),
                "status": nautobot.nested_str(circuit.get("status"), "name", "label", "display"),
                "tenant": nautobot.nested_str(circuit.get("tenant"), "name", "display"),
                "commit_rate": inventory.circuit_speed(circuit.get("commit_rate")),
                "term_side": term.get("term_side") or "",
                "port_speed": inventory.circuit_speed(term.get("port_speed")),
                "upstream_speed": inventory.circuit_speed(term.get("upstream_speed")),
                "xconnect_id": term.get("xconnect_id") or "",
                "pp_info": term.get("pp_info") or "",
                "description": term.get("description") or circuit.get("description") or "",
            }
        )
    circuits.sort(key=lambda item: (item["cid"].lower(), item["term_side"]))
    return circuits, ""


def address_circuits(location_id: str, terminated_ids: set) -> list[dict]:
    """The cached circuits without a termination whose description is this
    location's address (#383), after those terminated at it (*terminated_ids*,
    not repeated).

    ``shared_with``: how many other sites have the same address; a location and
    its parent or children at that address (a building of the site) are one
    site.  Without a database, or on an error: none.
    """
    conn = db.get_conn() if db.dialect() else None
    if conn is None:
        return []
    try:
        key, parents = inventory.read_locations_at_address(conn, location_id)
        circuits = inventory.read_circuits_at_address(conn, key)
    except Exception as exc:
        logger.warning("Could not match circuits by address for location %s: %s", location_id, exc)
        return []
    finally:
        conn.close()

    sites = {inventory.address_site(loc_id, parents) for loc_id in parents}
    shared_with = len(sites - {inventory.address_site(location_id, parents)})
    return [
        {**cached_circuit(circuit), "match": "address", "shared_with": shared_with}
        for circuit in circuits
        if circuit["circuit_id"] not in terminated_ids
    ]


def cached_circuit(circuit: dict) -> dict:
    """A stored circuit as the site panel and the circuit mapping show it (#383, #385)."""
    return {
        "id": circuit["circuit_id"],
        "cid": inventory.circuit_cid(circuit),
        "provider": circuit["provider"],
        "circuit_type": circuit["circuit_type"],
        "status": circuit["status"],
        "tenant": circuit["tenant"],
        "commit_rate": inventory.circuit_speed(circuit["commit_rate"]),
        "description": circuit["description"],
    }
