"""All SQL for items. Takes a session, returns ORM objects."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Item

UNIQUE_NAME_CONSTRAINT = "uq_items_name"


class DuplicateNameError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)  # psycopg
    return getattr(diag, "constraint_name", None)


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
        """Insert `item`. Raises `DuplicateNameError` if the name is taken (also when a
        concurrent request inserted it after the caller checked)."""
        self.session.add(item)
        try:
            self.session.flush()  # assigns id
        except IntegrityError as exc:
            if _constraint_name(exc) == UNIQUE_NAME_CONSTRAINT:
                raise DuplicateNameError(item.name) from exc
            raise
        self.session.refresh(item)  # loads server defaults (created_at)
        return item

    def delete(self, item: Item) -> None:
        self.session.delete(item)
        self.session.flush()
