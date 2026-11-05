from fastapi import APIRouter

from app.v1.items.controller import router as items_router

router = APIRouter(prefix="/api/v1")
router.include_router(items_router)
