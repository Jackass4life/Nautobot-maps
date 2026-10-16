"""Sites with missing or wrong coordinates (#368).

Such sites are missing from the map, or pull its first view (#362) out over
the sea, and are hard to find in Nautobot.  The check runs over the alert
board's rows: the sites the NOC watches.  A row without coordinates is listed
only when it has devices: regions and buildings above the sites have none by
design (they are rows when every location is one).

Problems, worst first:

- ``invalid``: latitude beyond ±90 or longitude beyond ±180;
- ``zero``: at 0,0, coordinates that were never set;
- ``swapped``: far from the rest, but not when latitude and longitude swap;
- ``far``: far from the rest (a hint to check: a lone site abroad is fine);
- ``missing``: no coordinates, so not on the map at all.

"Far" is more than ``max(FAR_MIN_KM, FAR_FACTOR × the median distance)``
from the median point of every site with coordinates, so it scales with how
spread out the network is.  The median is taken per axis: a network across
the 180° meridian gets a wrong centre (set ``MAP_DEFAULT_VIEW`` there, #362).
"""

import math
import statistics

from nautobot_maps import export, scope, status

FAR_MIN_KM = 2000
FAR_FACTOR = 4
# Fewer sites than this: no "far" (there is no "rest" to be far from).
FAR_MIN_SITES = 3

PROBLEM_ORDER = ("invalid", "zero", "swapped", "far", "missing")
REASONS = {
    "invalid": "Impossible coordinates: latitude beyond ±90 or longitude beyond ±180",
    "zero": "At 0,0: the coordinates were never set",
    "swapped": "Far from the other sites, but not with latitude and longitude swapped",
    "far": "Far from the other sites: check the coordinates",
    "missing": "No coordinates: not on the map",
}
CSV_COLUMNS = ("site_id", "site", "path", "type", "status", "problem", "reason", "latitude", "longitude", "km_away")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _coordinates(row: dict) -> tuple[float, float] | None:
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lon) if math.isfinite(lat) and math.isfinite(lon) else None


def _valid(point: tuple[float, float]) -> bool:
    return abs(point[0]) <= 90 and abs(point[1]) <= 180


def _has_devices(row: dict) -> bool:
    """Devices the board watches, in maintenance and down ones included: a
    region or building above the sites has none."""
    return any(row.get(field) for field in ("device_count", "maintenance_device_count", "down_device_count"))


def check(rows: list[dict], page_scope=None) -> list[dict]:
    """The rows with a coordinate problem, worst first, each the site's own
    fields plus ``problem``, ``reason`` and ``km_away``; only those in
    *page_scope* (``scope.read_scope``) when given."""
    placed = [(row, _coordinates(row)) for row in rows if scope.in_scope(row, page_scope)]
    real = [p for _, p in placed if p is not None and _valid(p) and p != (0.0, 0.0)]
    centre = limit = None
    if len(real) >= FAR_MIN_SITES:
        centre = (statistics.median(p[0] for p in real), statistics.median(p[1] for p in real))
        typical = statistics.median(distance_km(*point, *centre) for point in real)
        limit = max(FAR_MIN_KM, FAR_FACTOR * typical)

    problems = []
    for row, point in placed:
        km = None
        if point is not None and not _valid(point):
            problem = "invalid"
        elif point is None or point == (0.0, 0.0):
            # A region or building with no devices has none by design (and 0,0
            # is left out of the map's first view, #362); a site the board
            # watches and the map can't show is the problem.
            if not _has_devices(row):
                continue
            problem = "missing" if point is None else "zero"
        elif limit is not None and (km := distance_km(*point, *centre)) > limit:
            lat, lon = point
            swapped_ok = abs(lon) <= 90 and distance_km(lon, lat, *centre) <= limit
            problem = "swapped" if swapped_ok else "far"
        else:
            continue
        problems.append(
            {
                "site_id": row.get("id") or "",
                "name": row.get("name") or "",
                "ancestor_path": row.get("ancestor_path") or row.get("parent") or "",
                "type": row.get("location_type") or "",
                "status": row.get("status") or "",
                "latitude": point[0] if point else None,
                "longitude": point[1] if point else None,
                "problem": problem,
                "reason": REASONS[problem],
                "km_away": round(km) if km is not None else None,
                "tenants": status.site_tenants(row),
            }
        )
    problems.sort(key=lambda item: (PROBLEM_ORDER.index(item["problem"]), item["name"].lower()))
    return problems


def counts(problems: list[dict]) -> dict[str, int]:
    """``{problem: number of sites}`` for every problem, zeros included."""
    found = {problem: 0 for problem in PROBLEM_ORDER}
    for item in problems:
        found[item["problem"]] += 1
    return found


def csv_lines(problems: list[dict]):
    """The list as CSV, one row per site (the export's formula-safe lines)."""
    yield export.csv_header(CSV_COLUMNS)
    for item in problems:
        yield export.csv_line(
            [
                item["site_id"],
                item["name"],
                item["ancestor_path"],
                item["type"],
                item["status"],
                item["problem"],
                item["reason"],
                item["latitude"],
                item["longitude"],
                item["km_away"],
            ]
        )
