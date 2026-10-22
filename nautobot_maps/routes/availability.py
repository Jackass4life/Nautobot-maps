"""The template filters every Flask page may use (#342, #436).  Availability
itself is on FastAPI now (nautobot_maps/api/availability.py)."""

from flask import Blueprint

from nautobot_maps import templating

bp = Blueprint("availability", __name__)

for _name, _filter in templating.FILTERS.items():
    bp.add_app_template_filter(_filter, _name)
