"""The app's routes still on Flask, one blueprint per area (#424); web.py wires
them up.  Those moved to FastAPI are in nautobot_maps/api/ (#436)."""

from nautobot_maps.routes import (
    admin,
    availability,
    board,
    documentation,
    ingest,
    locations,
    maintenance,
    ops,
    pages,
)

# Registered in this order by web.register().
BLUEPRINTS = (
    pages.bp,
    locations.bp,
    board.bp,
    maintenance.bp,
    availability.bp,
    ingest.bp,
    admin.bp,
    documentation.bp,
    ops.bp,
)
