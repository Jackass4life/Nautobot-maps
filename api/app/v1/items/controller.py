"""HTTP layer for items: parse input, call the service, return DTOs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.repositories.item_repository import ItemRepository
from app.services.item_service import ItemService
from app.session import DbSession
from app.utils.errors import ERROR_RESPONSES
from app.v1.items.dtos import ItemCreateDto, ItemDto

router = APIRouter(prefix="/items", tags=["items"])


def get_item_service(db: DbSession) -> ItemService:
    return ItemService(ItemRepository(db))


Service = Annotated[ItemService, Depends(get_item_service)]


@router.get("", operation_id="listItems", response_model=list[ItemDto])
def list_items(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ItemDto]:
    return service.list_items(limit=limit, offset=offset)


@router.get(
    "/{item_id}",
    operation_id="getItem",
    response_model=ItemDto,
    responses={404: ERROR_RESPONSES[404]},
)
def get_item(item_id: int, service: Service) -> ItemDto:
    return service.get_item(item_id)


@router.post(
    "",
    operation_id="createItem",
    response_model=ItemDto,
    status_code=status.HTTP_201_CREATED,
    responses={409: ERROR_RESPONSES[409]},
)
def create_item(body: ItemCreateDto, service: Service) -> ItemDto:
    return service.create_item(body)


@router.delete(
    "/{item_id}",
    operation_id="deleteItem",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: ERROR_RESPONSES[404]},
)
def delete_item(item_id: int, service: Service) -> None:
    service.delete_item(item_id)
