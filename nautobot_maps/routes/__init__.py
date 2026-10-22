"""The app's routes, one blueprint per area (#424); web.py wires them up."""

from nautobot_maps.routes import ops, pages

# Registered in this order by web.register().
BLUEPRINTS = (pages.bp, ops.bp)
