"""The request's database session (#643), set by app/db/middleware.py."""

from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

db_session_ctx = ContextVar[AsyncSession | None]("db_session", default=None)
"""The database session opened for this request, if any."""
