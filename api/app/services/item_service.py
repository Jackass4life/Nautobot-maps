"""Item business logic: rules, plus ORM <-> DTO conversion."""

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Item
from app.repositories.item_repository import DuplicateNameError, ItemRepository
from app.v1.examples.dtos import ItemCreateDto, ItemDto


class ItemService:
    def __init__(self, repository: ItemRepository) -> None:
        self.repository = repository

    async def list_items(self, *, limit: int, offset: int) -> list[ItemDto]:
        return [_to_dto(i) for i in await self.repository.list(limit=limit, offset=offset)]

    async def get_item(self, item_id: int) -> ItemDto:
        item = await self.repository.get(item_id)
        if item is None:
            raise NotFoundError("Item", item_id)
        return _to_dto(item)

    async def create_item(self, data: ItemCreateDto) -> ItemDto:
        if await self.repository.get_by_name(data.name) is not None:
            raise _name_taken(data.name)
        try:
            item = await self.repository.add(Item(name=data.name, description=data.description))
        except DuplicateNameError as exc:  # inserted concurrently, after the check above
            raise _name_taken(data.name) from exc
        return _to_dto(item)

    async def delete_item(self, item_id: int) -> None:
        item = await self.repository.get(item_id)
        if item is None:
            raise NotFoundError("Item", item_id)
        await self.repository.delete(item.id)


def _name_taken(name: str) -> ConflictError:
    return ConflictError(f"Item named {name!r} already exists")


def _to_dto(item: Item) -> ItemDto:
    return ItemDto.model_validate(item)
