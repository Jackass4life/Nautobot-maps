"""All SQL for items. Takes a session, returns ORM objects."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item


class ItemRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, *, limit: int, offset: int) -> list[Item]:
        stmt = select(Item).order_by(Item.id).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def get(self, item_id: int) -> Item | None:
        return self.session.get(Item, item_id)

    def get_by_name(self, name: str) -> Item | None:
        return self.session.scalars(select(Item).where(Item.name == name)).first()

    def add(self, item: Item) -> Item:
        self.session.add(item)
        self.session.flush()  # assigns id
        self.session.refresh(item)  # loads server defaults (created_at)
        return item

    def delete(self, item: Item) -> None:
        self.session.delete(item)
        self.session.flush()
