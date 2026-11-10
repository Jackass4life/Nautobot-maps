"""The API by section (#623, #624): one folder per section of ``sections.py``, each
with its routes in ``controller.py`` and its request and answer models in
``dtos.py``; ``legacy/`` holds what no section owns.

Two kinds of router, kept as they are:
- the ones ported from the current app (#604) carry their full paths
  (``/api/v1/...``, and the pages', ``/healthz``'s, ``/metrics``'s): ``ROUTERS``,
  included on the app as they are;
- the new stack's own (#572, #592) are relative to ``/api/v1`` and go under
  ``router``, whose check_request applies the sign-in rules (#592): cross-site
  changes, rejected tokens, status tokens, AUTH_REQUIRE_VIEWER.
"""

from fastapi import APIRouter, Depends

from app.auth.dependencies import check_request
from app.v1.administration import controller as administration
from app.v1.alert_board import controller as alert_board
from app.v1.auth import controller as auth
from app.v1.availability import controller as availability
from app.v1.circuits import controller as circuits
from app.v1.documentation import controller as documentation
from app.v1.examples import controller as examples
from app.v1.incoming_alerts import controller as incoming_alerts
from app.v1.legacy import controller as legacy
from app.v1.maintenance import controller as maintenance
from app.v1.map import controller as map_controller
from app.v1.monitoring import controller as monitoring
from app.v1.shared import controller as shared
from app.v1.status_page import controller as status_page

router = APIRouter(prefix="/api/v1", dependencies=[Depends(check_request)])
router.include_router(administration.tokens_router)
router.include_router(administration.contract_router)
router.include_router(auth.router)
router.include_router(examples.router)

# In the menu's order; no two sections' paths overlap.
ROUTERS = (
    map_controller.router,
    alert_board.router,
    availability.router,
    maintenance.router,
    circuits.router,
    status_page.router,
    administration.router,
    documentation.router,
    documentation.spec_router,
    incoming_alerts.router,
    shared.router,
    monitoring.router,
    monitoring.health_router,
    legacy.router,
)


def install(app) -> None:  # type: ignore[no-untyped-def]
    """Every section's routes on *app*."""
    for section_router in ROUTERS:
        app.include_router(section_router)
    app.include_router(router)
