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
- ``missing``: no coordinates, so not on the map at all;
- ``far``: far from the rest, only a hint (a lone site abroad is fine), so
  last and not counted as wrong on the map (#416).

"The rest" is the site's own part of the location tree (#416): the closest
ancestor in its ``ancestor_path`` with at least ``FAR_MIN_SITES`` sites with
coordinates, else the whole network.  "Far" is more than
``max(minimum, FAR_FACTOR × the group's median distance)`` from the group's
median point, the minimum being ``FAR_MIN_KM`` for the whole network and
``REGION_FAR_MIN_KM`` in a region, so it scales with how spread out the
sites are.  The median is taken per axis: a group across the 180° meridian
gets a wrong centre (set ``MAP_DEFAULT_VIEW`` there, #362).
"""

import math
import statistics

from nautobot_maps import export, inventory, scope, status

FAR_MIN_KM = 2000
# Inside one region (#416): a Danish site placed in Spain is found; a lone
# site far away in the same region (the Faroes under Denmark) is a hint too.
REGION_FAR_MIN_KM = 1000
FAR_FACTOR = 4
# Fewer sites than this: no "far" (there is no "rest" to be far from).
FAR_MIN_SITES = 3

# "far" is a hint, not an error: after the sites that are wrong (#416).
PROBLEM_ORDER = ("invalid", "zero", "swapped", "missing", "far")
# What "far" is measured from when no region has enough sites.
WHOLE_NETWORK = "all sites"
REASONS = {
    "invalid": "Impossible coordinates: latitude beyond ±90 or longitude beyond ±180",
    "zero": "At 0,0: the coordinates were never set",
    "swapped": "{km:,} km from the other sites{where}, but not with latitude and longitude swapped",
    "far": "{km:,} km from the other sites{where}: check the coordinates",
    "missing": "No coordinates: not on the map",
}
CSV_COLUMNS = (
    "site_id",
    "site",
    "path",
    "type",
    "status",
    "problem",
    "reason",
    "latitude",
    "longitude",
    "km_from_centre",
    "compared_with",
)


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


def _groups(row: dict) -> list[str]:
    """The parts of the location tree *row* is in, closest first:
    "EMEA › DNK", then "EMEA"."""
    path = row.get("ancestor_path") or row.get("parent") or ""
    parts = [part for part in path.split(inventory.PATH_SEPARATOR) if part]
    return [inventory.PATH_SEPARATOR.join(parts[:n]) for n in range(len(parts), 0, -1)]


def _yardstick(points: list[tuple[float, float]], minimum: float) -> tuple[tuple[float, float], float]:
    """The median point of *points* and how far from it is "far"."""
    centre = (statistics.median(p[0] for p in points), statistics.median(p[1] for p in points))
    typical = statistics.median(distance_km(*point, *centre) for point in points)
    return centre, max(minimum, FAR_FACTOR * typical)


def _yardsticks(rows: list[dict]) -> dict:
    """``{group: (centre, limit)}`` for every part of the tree with enough
    sites with coordinates, and for the whole network (``WHOLE_NETWORK``).
    One pass over the rows, then one median per group (#416)."""
    points: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        point = _coordinates(row)
        if point is None or not _valid(point) or point == (0.0, 0.0):
            continue
        points.setdefault(WHOLE_NETWORK, []).append(point)
        for group in _groups(row):
            points.setdefault(group, []).append(point)
    return {
        group: _yardstick(found, FAR_MIN_KM if group == WHOLE_NETWORK else REGION_FAR_MIN_KM)
        for group, found in points.items()
        if len(found) >= FAR_MIN_SITES
    }


def check(rows: list[dict], page_scope=None) -> list[dict]:
    """The rows with a coordinate problem, worst first, each the site's own
    fields plus ``problem``, ``reason``, ``km_away`` and ``compared_with``
    (what it is far from); only those in *page_scope* (``scope.read_scope``)
    when given.  "The rest" is every site, not only the page's: a map for
    one tenant still compares a site with its whole region."""
    yardsticks = _yardsticks(rows)
    problems = []
    for row in rows:
        if not scope.in_scope(row, page_scope):
            continue
        point = _coordinates(row)
        km = group = None
        if point is not None and not _valid(point):
            problem = "invalid"
        elif point is None or point == (0.0, 0.0):
            # A region or building with no devices has none by design (and 0,0
            # is left out of the map's first view, #362); a site the board
            # watches and the map can't show is the problem.
            if not _has_devices(row):
                continue
            problem = "missing" if point is None else "zero"
        else:
            group = next((g for g in [*_groups(row), WHOLE_NETWORK] if g in yardsticks), None)
            if group is None:
                continue
            centre, limit = yardsticks[group]
            km = distance_km(*point, *centre)
            if km <= limit:
                continue
            lat, lon = point
            swapped_ok = abs(lon) <= 90 and distance_km(lon, lat, *centre) <= limit
            problem = "swapped" if swapped_ok else "far"
        km = round(km) if km is not None else None
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
                "reason": REASONS[problem].format(km=km, where=f" in {group}" if group != WHOLE_NETWORK else ""),
                "km_away": km,
                "compared_with": group or "",
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
                item["compared_with"],
            ]
        )
