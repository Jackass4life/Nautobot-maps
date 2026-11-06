"""The current app's migration record (nautobot_maps/db.py MIGRATIONS).

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    __table_args__ = (PrimaryKeyConstraint("version", name="schema_migrations_pkey"),)

    version: Mapped[int] = mapped_column(Integer, autoincrement=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
