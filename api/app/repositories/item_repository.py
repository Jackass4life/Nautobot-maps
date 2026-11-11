"""All SQL for items. Takes a session, returns ORM objects."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.repository import AsyncBaseRepository
from app.db.session import AsyncDbSession
from app.models import Item

UNIQUE_NAME_CONSTRAINT = "uq_items_name"


class DuplicateNameError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _constraint_name(exc: IntegrityError) -> str | None:
    # asyncpg's error, under SQLAlchemy's adapter (psycopg's had it in .diag).
    error = getattr(exc.orig, "__cause__", None)
    return getattr(error, "constraint_name", None)


class ItemRepository(AsyncBaseRepository[Item]):
    def __init__(self, db: AsyncDbSession) -> None:
        super().__init__(Item, db)

    async def list(self, *, limit: int, offset: int) -> list[Item]:
        return await self.get_all(select(Item).order_by(Item.id).limit(limit).offset(offset))

    async def get_by_name(self, name: str) -> Item | None:
        return await self.get_first(select(Item).where(Item.name == name))

    async def add(self, item: Item) -> Item:
        """Insert `item`. Raises `DuplicateNameError` if the name is taken (also when a
        concurrent request inserted it after the caller checked)."""
        try:
            return await self.create(item)
        except IntegrityError as exc:
            if _constraint_name(exc) == UNIQUE_NAME_CONSTRAINT:
                raise DuplicateNameError(item.name) from exc
            raise
