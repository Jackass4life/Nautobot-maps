"""Maintenance windows.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, PrimaryKeyConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="maintenance_windows_pkey"),
        Index("maintenance_windows_ended", "ended_at"),
        Index("maintenance_windows_time", "ends_at", "starts_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    batch_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
