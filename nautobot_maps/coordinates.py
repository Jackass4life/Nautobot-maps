"""Sites with missing or wrong coordinates (#368).

Such sites are missing from the map, or pull its first view (#362) out over
the sea, and are hard to find in Nautobot.  The check runs over the alert
board's rows: the sites the NOC watches.  A row without coordinates is listed
only when it has devices: regions and buildings above the sites have none by
design (they are rows when every location is one).

Problems, worst first:

- ``zero``: at 0,0, coordinates that were never set;
- ``swapped``: far from the rest, but not when latitude and longitude swap;
- ``far``: far from the rest (a hint to check: a lone site abroad is fine);
- ``missing``: no coordinates, so not on the map at all.

"Far" is more than ``max(FAR_MIN_KM, FAR_FACTOR × the median distance)``
from the median point of every site with coordinates, so it scales with how
spread out the network is.
"""

import math
import statistics

from nautobot_maps import status

FAR_MIN_KM = 2000
FAR_FACTOR = 4
# Fewer sites than this: no "far" (there is no "rest" to be far from).
FAR_MIN_SITES = 3

PROBLEM_ORDER = ("zero", "swapped", "far", "missing")
REASONS = {
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


def check(rows: list[dict]) -> list[dict]:
    """The rows with a coordinate problem, worst first, each
    ``{**site fields, problem, reason, km_away}``."""
    placed = [(row, point) for row in rows if (point := _coordinates(row)) is not None]
    real = [(row, point) for row, point in placed if point != (0.0, 0.0)]
    centre = limit = None
    if len(real) >= FAR_MIN_SITES:
        centre = (statistics.median(p[0] for _, p in real), statistics.median(p[1] for _, p in real))
        typical = statistics.median(distance_km(*point, *centre) for _, point in real)
        limit = max(FAR_MIN_KM, FAR_FACTOR * typical)

    problems = []
    for row in rows:
        point = _coordinates(row)
        km = None
        if point is None:
            # A region or building with no devices has none by design; a site with
            # devices the board watches and the map can't show is the problem.
            if not row.get("device_count"):
                continue
            problem = "missing"
        elif point == (0.0, 0.0):
            problem = "zero"
        elif limit is not None and (km := distance_km(*point, *centre)) > limit:
            lat, lon = point
            swapped_ok = abs(lon) <= 90 and distance_km(lon, lat, *centre) <= limit
            problem = "swapped" if swapped_ok else "far"
        else:
            continue
        problems.append(
            {
                "site_id": row.get("id", ""),
                "site": row.get("name", ""),
                "path": row.get("ancestor_path") or row.get("parent") or "",
                "type": row.get("location_type", ""),
                "status": row.get("status", ""),
                "latitude": point[0] if point else None,
                "longitude": point[1] if point else None,
                "problem": problem,
                "reason": REASONS[problem],
                "km_away": round(km) if km is not None else None,
                # For a map for some sites only (#367): what ?tenant= / ?q= match.
                "tenants": status.site_tenants(row),
                "physical_address": row.get("physical_address") or row.get("facility") or "",
                "country": row.get("country", ""),
            }
        )
    problems.sort(key=lambda item: (PROBLEM_ORDER.index(item["problem"]), item["site"].lower()))
    return problems


def counts(problems: list[dict]) -> dict[str, int]:
    """``{problem: number of sites}`` for every problem, zeros included."""
    found = {problem: 0 for problem in PROBLEM_ORDER}
    for item in problems:
        found[item["problem"]] += 1
    return found
