from fastapi import APIRouter, Depends

from app.utils.auth import refuse_cross_site_writes
from app.v1.items.controller import router as items_router
from app.v1.me.controller import router as me_router

router = APIRouter(prefix="/api/v1", dependencies=[Depends(refuse_cross_site_writes)])
router.include_router(items_router)
router.include_router(me_router)
