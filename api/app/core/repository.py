"""Our own async base repository (#643), in place of the example layout's private
``s_core.orm.AsyncBaseRepository``: the same methods, on one request's `AsyncSession`.

A repository flushes, never commits: the request's session commits when the answer
is a success (app/db/middleware.py).
"""

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession


class AsyncBaseRepository[T]:
    def __init__(self, model: type[T], db: AsyncSession) -> None:
        self.model = model
        self.db = db

    async def get(self, entity_id: Any) -> T | None:
        """The row with this primary key, or None."""
        return await self.db.get(self.model, entity_id)

    async def get_all(self, stmt: Select | None = None) -> list[T]:
        """The rows *stmt* selects; every row without one."""
        return list(await self.db.scalars(stmt if stmt is not None else select(self.model)))

    async def get_first(self, stmt: Select) -> T | None:
        return (await self.db.scalars(stmt.limit(1))).first()

    async def create(self, entity: T) -> T:
        """Insert *entity*; returned with its id and server defaults (created_at)."""
        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def save(self, entity: T) -> T:
        """Write *entity*'s changes."""
        entity = await self.db.merge(entity)
        await self.db.flush()
        return entity

    async def delete(self, entity_id: Any) -> bool:
        """Delete the row with this primary key; False when there is none."""
        entity = await self.get(entity_id)
        if entity is None:
            return False
        await self.db.delete(entity)
        await self.db.flush()
        return True
