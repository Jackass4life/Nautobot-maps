"""The app's routes, one blueprint per area (#424); web.py wires them up."""

from nautobot_maps.routes import (
    admin,
    availability,
    board,
    circuits,
    documentation,
    ingest,
    locations,
    maintenance,
    ops,
    pages,
    status,
)

# Registered in this order by web.register().
BLUEPRINTS = (
    pages.bp,
    locations.bp,
    board.bp,
    maintenance.bp,
    availability.bp,
    circuits.bp,
    status.bp,
    ingest.bp,
    admin.bp,
    documentation.bp,
    ops.bp,
)
