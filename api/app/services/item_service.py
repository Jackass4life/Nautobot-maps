"""Item business logic: rules, plus ORM <-> DTO conversion."""

from app.models import Item
from app.repositories.item_repository import ItemRepository
from app.utils.errors import ConflictError, NotFoundError
from app.v1.items.dtos import ItemCreateDto, ItemDto


class ItemService:
    def __init__(self, repository: ItemRepository) -> None:
        self.repository = repository

    def list_items(self, *, limit: int, offset: int) -> list[ItemDto]:
        return [_to_dto(i) for i in self.repository.list(limit=limit, offset=offset)]

    def get_item(self, item_id: int) -> ItemDto:
        item = self.repository.get(item_id)
        if item is None:
            raise NotFoundError("Item", item_id)
        return _to_dto(item)

    def create_item(self, data: ItemCreateDto) -> ItemDto:
        if self.repository.get_by_name(data.name) is not None:
            raise ConflictError(f"Item named {data.name!r} already exists")
        item = self.repository.add(Item(name=data.name, description=data.description))
        return _to_dto(item)

    def delete_item(self, item_id: int) -> None:
        item = self.repository.get(item_id)
        if item is None:
            raise NotFoundError("Item", item_id)
        self.repository.delete(item)


def _to_dto(item: Item) -> ItemDto:
    return ItemDto.model_validate(item)
