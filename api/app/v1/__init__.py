from fastapi import APIRouter, Depends

from app.utils.auth import check_request
from app.v1.items.controller import router as items_router
from app.v1.me.controller import router as me_router
from app.v1.tokens.controller import router as tokens_router

# check_request: cross-site changes, rejected tokens, status tokens, AUTH_REQUIRE_VIEWER (#592).
router = APIRouter(prefix="/api/v1", dependencies=[Depends(check_request)])
router.include_router(items_router)
router.include_router(me_router)
router.include_router(tokens_router)
