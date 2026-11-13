"""HTTP layer for items: parse input, call the service, return DTOs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.core.exceptions import ERROR_RESPONSES
from app.db.session import AsyncDbSession
from app.repositories.item_repository import ItemRepository
from app.services.item_service import ItemService
from app.v1 import sections
from app.v1.examples.dtos import ItemCreateDto, ItemDto

router = APIRouter(prefix="/items", tags=sections.tag("examples"))

WRITE_RESPONSES = {401: ERROR_RESPONSES[401], 403: ERROR_RESPONSES[403]}


def get_item_service(db: AsyncDbSession) -> ItemService:
    return ItemService(ItemRepository(db))


Service = Annotated[ItemService, Depends(get_item_service)]


@router.get(
    "",
    dependencies=[Depends(require_permission(Permission.SESSION_BASIC))],
    operation_id="listItems",
    response_model=list[ItemDto],
    responses={401: ERROR_RESPONSES[401], 403: ERROR_RESPONSES[403]},
)
async def list_items(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ItemDto]:
    return await service.list_items(limit=limit, offset=offset)


@router.get(
    "/{item_id}",
    dependencies=[Depends(require_permission(Permission.SESSION_BASIC))],
    operation_id="getItem",
    response_model=ItemDto,
    responses={401: ERROR_RESPONSES[401], 403: ERROR_RESPONSES[403], 404: ERROR_RESPONSES[404]},
)
async def get_item(item_id: int, service: Service) -> ItemDto:
    return await service.get_item(item_id)


@router.post(
    "",
    operation_id="createItem",
    response_model=ItemDto,
    status_code=status.HTTP_201_CREATED,
    responses={**WRITE_RESPONSES, 409: ERROR_RESPONSES[409]},
    # The example is open with AUTH_MODE=disabled, as the board's own changes are.
    dependencies=[Depends(require_permission(Permission.EXAMPLES_WRITE, open_when_disabled=True))],
)
async def create_item(body: ItemCreateDto, service: Service) -> ItemDto:
    return await service.create_item(body)


@router.delete(
    "/{item_id}",
    operation_id="deleteItem",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**WRITE_RESPONSES, 404: ERROR_RESPONSES[404]},
    # The example is open with AUTH_MODE=disabled, as the board's own changes are.
    dependencies=[Depends(require_permission(Permission.EXAMPLES_WRITE, open_when_disabled=True))],
)
async def delete_item(item_id: int, service: Service) -> None:
    await service.delete_item(item_id)
