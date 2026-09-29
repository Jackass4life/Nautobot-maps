"""Inventory sync: Nautobot and LibreNMS into the PostgreSQL cache tables (#165).

Normalises Nautobot locations and devices, reads and writes the cache tables,
tracks sync state, and runs the syncs (``inventory.ensure_snapshot()``).
Called as ``inventory.function()`` so tests can replace it on this module.
"""

import ipaddress
import json
import logging
import threading
from datetime import UTC, datetime

from nautobot_maps import caching, db, librenms, nautobot, settings, timeutil

logger = logging.getLogger(__name__)

FULL_RECONCILE_INTERVAL_SECONDS = 86400
# Bumped when cached fields change, forcing one full Nautobot resync
# (3: locations store parent_id, #158).
CACHE_VERSION = "3"
sync_lock = threading.Lock()


def json_load_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def normalize_locations(
    raw: list,
    include_without_coordinates: bool = False,
    existing_location_name_map: dict | None = None,
) -> list:
    tenant_map = nautobot.id_name_map("tenancy/tenants/")
    status_map = nautobot.id_name_map("extras/statuses/")
    lt_map = nautobot.id_name_map("dcim/location-types/")
    tag_map = nautobot.id_name_map("extras/tags/")
    tenant_group_map = nautobot.tenant_group_map()

    loc_name_map = dict(existing_location_name_map or {})
    for loc in raw:
        uid = loc.get("id")
        if uid:
            name = nautobot.nested_str(loc, "name", "display")
            if name:
                loc_name_map[uid] = name

    if raw:
        logger.debug(
            "Nautobot location sample – tenant=%r  status=%r",
            raw[0].get("tenant"),
            raw[0].get("status"),
        )

    def _extract_location_country(location: dict, physical_address: str) -> str:
        country_obj = location.get("country")
        if isinstance(country_obj, dict):
            country_name = nautobot.nested_str(country_obj, "name", "display", "label")
            if country_name:
                return country_name
        elif isinstance(country_obj, str) and country_obj.strip():
            return country_obj.strip()

        raw_country = location.get("country_name")
        if isinstance(raw_country, str) and raw_country.strip():
            return raw_country.strip()

        if physical_address:
            parts = [part.strip() for part in physical_address.split(",") if part.strip()]
            if parts:
                return parts[-1]
        return ""

    locations = []
    for loc in raw:
        lat = None
        lon = None
        raw_lat = loc.get("latitude")
        raw_lon = loc.get("longitude")
        has_coordinates = raw_lat is not None and raw_lon is not None
        if has_coordinates:
            try:
                lat = float(raw_lat)
                lon = float(raw_lon)
            except (TypeError, ValueError):
                has_coordinates = False
                lat = None
                lon = None
        if not has_coordinates and not include_without_coordinates:
            continue

        tenant_obj = loc.get("tenant") or {}
        tenant_id = tenant_obj.get("id", "") if isinstance(tenant_obj, dict) else ""
        tenant_name = nautobot.nested_str(tenant_obj, "name", "display") or tenant_map.get(tenant_id, "")

        status_obj = loc.get("status") or {}
        status_id = status_obj.get("id", "") if isinstance(status_obj, dict) else ""
        status_name = nautobot.nested_str(status_obj, "label", "name", "display") or status_map.get(status_id, "")

        lt_obj = loc.get("location_type") or {}
        lt_id = lt_obj.get("id", "") if isinstance(lt_obj, dict) else ""
        location_type_name = nautobot.nested_str(lt_obj, "name", "display") or lt_map.get(lt_id, "")

        parent_obj = loc.get("parent") or {}
        parent_id = parent_obj.get("id", "") if isinstance(parent_obj, dict) else ""
        parent_name = nautobot.nested_str(parent_obj, "name", "display") or loc_name_map.get(parent_id, "")

        tenant_group_name = tenant_group_map.get(tenant_id, "")
        raw_tags = loc.get("tags") or []
        tag_names = []
        for t in raw_tags:
            if isinstance(t, dict):
                tag_id = t.get("id", "")
                tag_name = nautobot.nested_str(t, "name", "display") or tag_map.get(tag_id, "")
            else:
                tag_name = ""
            if tag_name:
                tag_names.append(tag_name)

        physical_address = (loc.get("physical_address") or "").strip()
        country = _extract_location_country(loc, physical_address)

        locations.append(
            {
                "id": loc.get("id", ""),
                "name": loc.get("name", "Unknown"),
                "slug": loc.get("slug", ""),
                "status": status_name,
                "location_type": location_type_name,
                "parent": parent_name,
                "parent_id": parent_id,
                "latitude": lat,
                "longitude": lon,
                "description": loc.get("description", ""),
                "physical_address": physical_address,
                "country": country,
                "facility": loc.get("facility", ""),
                "tenant": tenant_name,
                "tenant_id": tenant_id,
                "tenant_group": tenant_group_name,
                "asn": loc.get("asn"),
                "time_zone": loc.get("time_zone", ""),
                "tags": tag_names,
                "url": loc.get("url", ""),
                "last_updated": loc.get("last_updated") or "",
            }
        )
    return locations


def extract_primary_ip(device: dict) -> str:
    for key in ("primary_ip4", "primary_ip6", "primary_ip"):
        value = device.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        extracted = nautobot.nested_str(value, "host", "address", "display", "name")
        if extracted:
            return extracted
    return ""


def librenms_host_key(value: str) -> str:
    return (value or "").strip().lower().split(".", 1)[0]


def librenms_ip_key(value: str) -> str:
    candidate = (value or "").strip().split("/", 1)[0]
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return candidate


def is_ip_literal(value: str) -> bool:
    candidate = librenms_ip_key(value)
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def normalize_devices(devices_data: list, lookup_maps: dict | None = None) -> list:
    if lookup_maps is None:
        lookup_maps = nautobot.device_lookup_maps()
    dt_mfr_map = lookup_maps.get("dt_mfr_map", {})
    dt_model_map = lookup_maps.get("dt_model_map", {})
    mfr_map = lookup_maps.get("mfr_map", {})
    role_map = lookup_maps.get("role_map", {})
    tenant_map = lookup_maps.get("tenant_map", {})
    status_map = lookup_maps.get("status_map", {})

    devices = []
    for d in devices_data:
        dt = d.get("device_type") or {}
        dt_id = dt.get("id", "") if isinstance(dt, dict) else ""
        mfr_obj = dt.get("manufacturer") if isinstance(dt, dict) else None
        mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
        mfr_name = (
            nautobot.nested_str(mfr_obj, "name", "display") or mfr_map.get(mfr_id, "") or dt_mfr_map.get(dt_id, "")
        )

        ten_obj = d.get("tenant") or {}
        ten_id = ten_obj.get("id", "") if isinstance(ten_obj, dict) else ""
        ten_name = nautobot.nested_str(ten_obj, "name", "display") or tenant_map.get(ten_id, "")

        st_obj = d.get("status") or {}
        st_id = st_obj.get("id", "") if isinstance(st_obj, dict) else ""
        st_name = nautobot.nested_str(st_obj, "label", "name", "display") or status_map.get(st_id, "")

        devices.append(
            {
                "id": d.get("id") or "",
                "name": d.get("name") or "Unknown",
                "device_type": (
                    nautobot.nested_str(d.get("device_type"), "model", "display") or dt_model_map.get(dt_id, "")
                ),
                "manufacturer": mfr_name,
                "role": (
                    nautobot.nested_str(d.get("role"), "name", "display")
                    or role_map.get(
                        d.get("role", {}).get("id", "") if isinstance(d.get("role"), dict) else "",
                        "",
                    )
                ),
                "status": st_name,
                "primary_ip": extract_primary_ip(d),
                "platform": nautobot.nested_str(d.get("platform"), "name", "display"),
                "serial": d.get("serial") or "",
                "tenant": ten_name,
                "last_updated": d.get("last_updated") or "",
                "location_id": (d.get("location", {}).get("id", "") if isinstance(d.get("location"), dict) else ""),
            }
        )
    return devices


def read_location_name_map(conn=None) -> dict:
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT location_id, name FROM nautobot_location_cache").fetchall()
        return {
            db.row_to_dict(row).get("location_id", ""): db.row_to_dict(row).get("name", "")
            for row in rows
            if db.row_to_dict(row).get("location_id")
        }
    except Exception as exc:
        logger.debug("Could not read cached location names: %s", exc)
        return {}
    finally:
        if owns_conn:
            conn.close()


def read_locations(include_without_coordinates: bool = False, conn=None) -> list:
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT location_id, name, slug, status, location_type, parent, parent_id, latitude, longitude,
                   description, physical_address, facility, tenant, tenant_id, tenant_group, asn,
                   time_zone, tags_json, url
            FROM nautobot_location_cache
            ORDER BY name ASC
            """
        ).fetchall()
        locations = []
        for row in rows:
            data = db.row_to_dict(row)
            lat = data.get("latitude")
            lon = data.get("longitude")
            has_coordinates = lat is not None and lon is not None
            if not has_coordinates and not include_without_coordinates:
                continue
            locations.append(
                {
                    "id": data.get("location_id", ""),
                    "name": data.get("name", ""),
                    "slug": data.get("slug", ""),
                    "status": data.get("status", ""),
                    "location_type": data.get("location_type", ""),
                    "parent": data.get("parent", ""),
                    "parent_id": data.get("parent_id", ""),
                    "latitude": lat,
                    "longitude": lon,
                    "description": data.get("description", ""),
                    "physical_address": data.get("physical_address", ""),
                    "facility": data.get("facility", ""),
                    "tenant": data.get("tenant", ""),
                    "tenant_id": data.get("tenant_id", ""),
                    "tenant_group": data.get("tenant_group", ""),
                    "asn": data.get("asn"),
                    "time_zone": data.get("time_zone", ""),
                    "tags": json_load_list(data.get("tags_json")),
                    "url": data.get("url", ""),
                }
            )
        return locations
    except Exception as exc:
        logger.debug("Could not read cached locations: %s", exc)
        return []
    finally:
        if owns_conn:
            conn.close()


def read_devices(location_id: str | None = None, conn=None) -> list:
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return []
    try:
        params = ()
        if location_id:
            marker = db.placeholders(1)
            query = (
                "SELECT device_id, location_id, name, device_type, manufacturer, role, status, "
                "primary_ip, platform, serial, tenant, last_updated FROM nautobot_device_cache "
                f"WHERE location_id = {marker} ORDER BY name ASC"
            )
            params = (location_id,)
        else:
            query = (
                "SELECT device_id, location_id, name, device_type, manufacturer, role, status, "
                "primary_ip, platform, serial, tenant, last_updated FROM nautobot_device_cache "
                "ORDER BY location_id, name ASC"
            )
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": data.get("device_id", ""),
                "location_id": data.get("location_id", ""),
                "name": data.get("name", ""),
                "device_type": data.get("device_type", ""),
                "manufacturer": data.get("manufacturer", ""),
                "role": data.get("role", ""),
                "status": data.get("status", ""),
                "primary_ip": data.get("primary_ip", ""),
                "platform": data.get("platform", ""),
                "serial": data.get("serial", ""),
                "tenant": data.get("tenant", ""),
                # When Nautobot last changed the device, e.g. its status (#166).
                "last_updated": data.get("last_updated") or "",
            }
            for data in (db.row_to_dict(row) for row in rows)
        ]
    except Exception as exc:
        logger.debug("Could not read cached devices: %s", exc)
        return []
    finally:
        if owns_conn:
            conn.close()


def read_librenms_devices(conn=None) -> list:
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT device_id, hostname, ip, status FROM librenms_device_status ORDER BY hostname ASC"
        ).fetchall()
        return [
            {
                "device_id": data.get("device_id"),
                "hostname": data.get("hostname", ""),
                "ip": data.get("ip", ""),
                "status": data.get("status"),
            }
            for data in (db.row_to_dict(row) for row in rows)
        ]
    except Exception as exc:
        logger.debug("Could not read cached LibreNMS inventory: %s", exc)
        return []
    finally:
        if owns_conn:
            conn.close()


def record_sync_state(
    conn,
    source: str,
    *,
    last_started_at: str | None = None,
    last_completed_at: str | None = None,
    last_successful_sync: str | None = None,
    cache_version: str = "",
    status: str = "idle",
    error_message: str = "",
) -> None:
    p0, p1, p2, p3, p4, p5, p6 = db.placeholders(7).split(",")
    conn.execute(
        f"""
        INSERT INTO inventory_sync_state
            (source, last_started_at, last_completed_at, last_successful_sync, cache_version, status, error_message)
        VALUES ({p0}, {p1}, {p2}, {p3}, {p4}, {p5}, {p6})
        ON CONFLICT(source) DO UPDATE SET
            last_started_at = excluded.last_started_at,
            last_completed_at = excluded.last_completed_at,
            last_successful_sync = excluded.last_successful_sync,
            cache_version = excluded.cache_version,
            status = excluded.status,
            error_message = excluded.error_message
        """,
        (
            source,
            last_started_at,
            last_completed_at,
            last_successful_sync,
            cache_version,
            status,
            error_message,
        ),
    )


def get_sync_state(source: str, conn=None) -> dict:
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
    if conn is None:
        return {}
    try:
        marker = db.placeholders(1)
        row = conn.execute(
            f"""
            SELECT source, last_started_at, last_completed_at, last_successful_sync, cache_version, status, error_message
            FROM inventory_sync_state
            WHERE source = {marker}
            """,
            (source,),
        ).fetchone()
        return db.row_to_dict(row)
    except Exception as exc:
        logger.debug("Could not read sync state for %s: %s", source, exc)
        return {}
    finally:
        if owns_conn:
            conn.close()


def sync_due(source: str, interval_seconds: int, conn=None) -> bool:
    state = get_sync_state(source, conn=conn)
    if not state:
        return True
    if state.get("status") == "running":
        started_at = timeutil.parse_iso_datetime(state.get("last_started_at"))
        if started_at is None:
            return True
        stale_after = max(900, interval_seconds * 2)
        return (datetime.now(UTC) - started_at).total_seconds() >= stale_after
    completed_at = timeutil.parse_iso_datetime(state.get("last_completed_at"))
    if completed_at is None:
        return True
    return (datetime.now(UTC) - completed_at).total_seconds() >= max(0, interval_seconds)


def cache_version_mismatch(state: dict | None) -> bool:
    return (state or {}).get("cache_version") != CACHE_VERSION


def snapshot_initialized(conn=None) -> bool:
    """Return whether Nautobot inventory snapshot has completed at least once."""
    state = get_sync_state("nautobot_inventory", conn=conn)
    if not state:
        return False
    return bool(state.get("status") == "idle" and state.get("last_completed_at"))


def coalesce_text(value):
    return "" if value is None else value


def write_locations(conn, locations: list) -> None:
    placeholders = db.placeholders(20).split(",")
    for loc in locations:
        conn.execute(
            f"""
            INSERT INTO nautobot_location_cache
                (location_id, name, slug, status, location_type, parent, parent_id, latitude, longitude,
                 description, physical_address, facility, tenant, tenant_id, tenant_group, asn,
                 time_zone, tags_json, url, last_updated, synced_at)
            VALUES ({", ".join(placeholders)}, {db.sql_now()})
            ON CONFLICT(location_id) DO UPDATE SET
                name = excluded.name,
                slug = excluded.slug,
                status = excluded.status,
                location_type = excluded.location_type,
                parent = excluded.parent,
                parent_id = excluded.parent_id,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                description = excluded.description,
                physical_address = excluded.physical_address,
                facility = excluded.facility,
                tenant = excluded.tenant,
                tenant_id = excluded.tenant_id,
                tenant_group = excluded.tenant_group,
                asn = excluded.asn,
                time_zone = excluded.time_zone,
                tags_json = excluded.tags_json,
                url = excluded.url,
                last_updated = excluded.last_updated,
                synced_at = excluded.synced_at
            """,
            (
                coalesce_text(loc.get("id", "")),
                coalesce_text(loc.get("name", "")),
                coalesce_text(loc.get("slug", "")),
                coalesce_text(loc.get("status", "")),
                coalesce_text(loc.get("location_type", "")),
                coalesce_text(loc.get("parent", "")),
                coalesce_text(loc.get("parent_id", "")),
                loc.get("latitude"),
                loc.get("longitude"),
                coalesce_text(loc.get("description", "")),
                coalesce_text(loc.get("physical_address", "")),
                coalesce_text(loc.get("facility", "")),
                coalesce_text(loc.get("tenant", "")),
                coalesce_text(loc.get("tenant_id", "")),
                coalesce_text(loc.get("tenant_group", "")),
                loc.get("asn"),
                loc.get("time_zone"),
                json.dumps(loc.get("tags", []), separators=(",", ":"), sort_keys=True),
                coalesce_text(loc.get("url", "")),
                loc.get("last_updated") or None,
            ),
        )


def write_devices(conn, devices: list) -> None:
    placeholders = db.placeholders(12).split(",")
    for device in devices:
        conn.execute(
            f"""
            INSERT INTO nautobot_device_cache
                (device_id, location_id, name, device_type, manufacturer, role, status,
                 primary_ip, platform, serial, tenant, last_updated, synced_at)
            VALUES ({", ".join(placeholders)}, {db.sql_now()})
            ON CONFLICT(device_id) DO UPDATE SET
                location_id = excluded.location_id,
                name = excluded.name,
                device_type = excluded.device_type,
                manufacturer = excluded.manufacturer,
                role = excluded.role,
                status = excluded.status,
                primary_ip = excluded.primary_ip,
                platform = excluded.platform,
                serial = excluded.serial,
                tenant = excluded.tenant,
                last_updated = excluded.last_updated,
                synced_at = excluded.synced_at
            """,
            (
                coalesce_text(device.get("id", "")),
                coalesce_text(device.get("location_id", "")),
                coalesce_text(device.get("name", "")),
                coalesce_text(device.get("device_type", "")),
                coalesce_text(device.get("manufacturer", "")),
                coalesce_text(device.get("role", "")),
                coalesce_text(device.get("status", "")),
                coalesce_text(device.get("primary_ip", "")),
                coalesce_text(device.get("platform", "")),
                coalesce_text(device.get("serial", "")),
                coalesce_text(device.get("tenant", "")),
                device.get("last_updated") or None,
            ),
        )


def librenms_polled_ip(device: dict) -> str:
    """Return the address LibreNMS polls *device* on, or ``""``.

    LibreNMS ``list_devices`` returns ``overwrite_ip`` (an operator-set
    polling address) and ``ip`` (the device's resolved IP); the override wins.
    Only valid IP literals are returned.
    """
    for key in ("overwrite_ip", "ip"):
        value = device.get(key)
        if isinstance(value, str) and is_ip_literal(value):
            return librenms_ip_key(value)
    return ""


def write_librenms_devices(conn, devices: list) -> None:
    placeholders = db.placeholders(6).split(",")
    for device in devices:
        conn.execute(
            f"""
            INSERT INTO librenms_device_status
                (device_id, hostname, ip, status, status_raw, status_reason, synced_at)
            VALUES ({", ".join(placeholders)}, {db.sql_now()})
            ON CONFLICT(device_id) DO UPDATE SET
                hostname = excluded.hostname,
                ip = excluded.ip,
                status = excluded.status,
                status_raw = excluded.status_raw,
                status_reason = excluded.status_reason,
                synced_at = excluded.synced_at
            """,
            (
                device.get("device_id"),
                device.get("hostname", ""),
                librenms_polled_ip(device),
                device.get("status"),
                str(device.get("status", "")),
                device.get("status_reason", "") or "",
            ),
        )


def sync_nautobot(force: bool = False) -> None:
    conn = db.get_conn()
    if conn is None or not settings.NAUTOBOT_URL or not settings.NAUTOBOT_TOKEN:
        if conn is not None:
            conn.close()
        return
    source = "nautobot_inventory"
    reconcile_source = "nautobot_inventory_reconcile"
    started_at = timeutil.iso_utc_now()
    last_successful_sync = None
    source_state = {}
    try:
        source_state = {} if force else get_sync_state(source, conn=conn)
        last_successful_sync = None if force else source_state.get("last_successful_sync")
        version_mismatch = cache_version_mismatch(source_state)
        full_reconcile = (
            force
            or version_mismatch
            or not last_successful_sync
            or sync_due(
                reconcile_source,
                FULL_RECONCILE_INTERVAL_SECONDS,
                conn=conn,
            )
        )
        with db.transaction(conn):
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=None,
                last_successful_sync=last_successful_sync,
                cache_version=source_state.get("cache_version", ""),
                status="running",
                error_message="",
            )

        params = {}
        if last_successful_sync and not full_reconcile:
            params["last_updated__gte"] = last_successful_sync
        raw_locations = nautobot.fetch_all_pages("dcim/locations/", params or None)
        device_params = dict(params)
        device_params["depth"] = 1
        raw_devices = nautobot.fetch_all_pages("dcim/devices/", device_params or None)
        if full_reconcile:
            existing_counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM nautobot_location_cache) AS location_count,
                    (SELECT COUNT(*) FROM nautobot_device_cache) AS device_count
                """
            ).fetchone()
            existing_counts = db.row_to_dict(existing_counts)
            if (
                (
                    int(existing_counts.get("location_count") or 0) > 0
                    or int(existing_counts.get("device_count") or 0) > 0
                )
                and not raw_locations
                and not raw_devices
            ):
                raise RuntimeError(
                    "Full inventory reconcile returned an empty dataset; keeping the existing cached snapshot"
                )
        existing_location_name_map = read_location_name_map(conn=conn)
        locations = normalize_locations(
            raw_locations,
            include_without_coordinates=True,
            existing_location_name_map=existing_location_name_map,
        )
        devices = normalize_devices(raw_devices, lookup_maps=nautobot.device_lookup_maps())
        completed_at = timeutil.iso_utc_now()
        watermark = last_successful_sync
        observed_last_updated = timeutil.max_last_updated(raw_locations + raw_devices)
        observed_dt = timeutil.parse_iso_datetime(observed_last_updated)
        current_dt = timeutil.parse_iso_datetime(watermark)
        if observed_dt and (current_dt is None or observed_dt > current_dt):
            watermark = timeutil.next_watermark(observed_last_updated)
        if full_reconcile:
            watermark = timeutil.max_iso_datetime_value(watermark, started_at) or started_at
        with db.transaction(conn):
            if full_reconcile:
                conn.execute("DELETE FROM nautobot_device_cache")
                conn.execute("DELETE FROM nautobot_location_cache")
            write_locations(conn, locations)
            write_devices(conn, devices)
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=completed_at,
                last_successful_sync=watermark,
                cache_version=CACHE_VERSION,
                status="idle",
                error_message="",
            )
            if full_reconcile:
                record_sync_state(
                    conn,
                    reconcile_source,
                    last_started_at=started_at,
                    last_completed_at=completed_at,
                    last_successful_sync=watermark,
                    cache_version=CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        caching.invalidate_alert_board()
    except Exception as exc:
        logger.warning("Could not sync Nautobot inventory into persistence DB: %s", exc, exc_info=True)
        with db.transaction(conn):
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=timeutil.iso_utc_now(),
                last_successful_sync=last_successful_sync,
                cache_version=source_state.get("cache_version", ""),
                status="error",
                error_message=str(exc),
            )
    finally:
        conn.close()


def sync_librenms(force: bool = False) -> None:
    conn = db.get_conn()
    if conn is None or not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        if conn is not None:
            conn.close()
        return
    source = "librenms_inventory"
    started_at = timeutil.iso_utc_now()
    last_successful_sync = None
    try:
        last_successful_sync = None if force else get_sync_state(source, conn=conn).get("last_successful_sync")
        with db.transaction(conn):
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=None,
                last_successful_sync=last_successful_sync,
                status="running",
                error_message="",
            )
        devices = librenms.fetch_inventory()
        existing_count = conn.execute("SELECT COUNT(*) AS device_count FROM librenms_device_status").fetchone()
        existing_count = int(db.row_to_dict(existing_count).get("device_count") or 0)
        if existing_count > 0 and not devices:
            raise RuntimeError("LibreNMS refresh returned an empty dataset; keeping the existing cached snapshot")
        with db.transaction(conn):
            completed_at = timeutil.iso_utc_now()
            conn.execute("DELETE FROM librenms_device_status")
            write_librenms_devices(conn, devices)
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=completed_at,
                last_successful_sync=completed_at,
                status="idle",
                error_message="",
            )
        caching.invalidate_alert_board()
    except Exception as exc:
        logger.warning("Could not sync LibreNMS inventory into persistence DB: %s", exc, exc_info=True)
        with db.transaction(conn):
            record_sync_state(
                conn,
                source,
                last_started_at=started_at,
                last_completed_at=timeutil.iso_utc_now(),
                last_successful_sync=None if force else last_successful_sync,
                status="error",
                error_message=str(exc),
            )
    finally:
        conn.close()


def ensure_snapshot(force: bool = False, wait: bool = False, full: bool | None = None) -> bool:
    """Run the inventory syncs that are due, in the background unless *wait*.

    ``force`` runs them now even if their interval has not passed.  ``full``
    makes the Nautobot sync a full reconcile (re-fetch everything, ignoring
    the watermark); it defaults to ``force``.  Pass ``force=True, full=False``
    for a cheap "sync now" that only pulls changes since the last sync.
    Returns whether a sync was started (or, with *wait*, performed).
    """
    if full is None:
        full = force

    def _run_with_lock() -> bool:
        conn = None
        release_db_lock = None
        try:
            release_db_lock = db.try_advisory_lock("inventory_snapshot_sync")
            if release_db_lock is False:
                return False
            conn = db.get_conn()
            if conn is None:
                return False
            nautobot_state = get_sync_state("nautobot_inventory", conn=conn)
            needs_nautobot = bool(settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN) and (
                force
                or cache_version_mismatch(nautobot_state)
                or sync_due(
                    "nautobot_inventory",
                    settings.INVENTORY_SYNC_INTERVAL_SECONDS,
                    conn=conn,
                )
            )
            needs_librenms = bool(
                (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip()
            ) and (
                force
                or sync_due(
                    "librenms_inventory",
                    settings.LIBRENMS_SYNC_INTERVAL_SECONDS,
                    conn=conn,
                )
            )
            if not needs_nautobot and not needs_librenms:
                return False
            if needs_nautobot:
                sync_nautobot(force=full)
            if needs_librenms:
                sync_librenms(force=full)
            return True
        finally:
            if conn is not None:
                conn.close()
            if callable(release_db_lock):
                release_db_lock()
            sync_lock.release()

    if wait:
        sync_lock.acquire()
        return _run_with_lock()
    if not sync_lock.acquire(blocking=False):
        return False
    threading.Thread(target=_run_with_lock, daemon=True).start()
    return True


def get_locations(include_without_coordinates: bool = False, snapshot_only: bool = False) -> list:
    """Fetch locations from Nautobot.

    By default, only locations with valid GPS coordinates are returned.
    Set ``include_without_coordinates=True`` to include all locations and
    keep missing/invalid coordinates as ``None``.
    """
    cached = read_locations(include_without_coordinates=include_without_coordinates)
    if cached:
        if not snapshot_only:
            ensure_snapshot()
        return cached
    if snapshot_only:
        return []
    ensure_snapshot(force=True, wait=True)
    cached = read_locations(include_without_coordinates=include_without_coordinates)
    if cached:
        return cached
    raw = nautobot.fetch_all_pages("dcim/locations/")
    return normalize_locations(raw, include_without_coordinates=include_without_coordinates)
