"""The app's routes still on Flask, one blueprint per area (#424); web.py wires
them up.  Those moved to FastAPI are in nautobot_maps/api/ (#436)."""

from nautobot_maps.routes import (
    availability,
    documentation,
    pages,
)

# Registered in this order by web.register().
BLUEPRINTS = (
    pages.bp,
    availability.bp,
    documentation.bp,
)
