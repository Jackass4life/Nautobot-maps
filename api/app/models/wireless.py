"""Access point status, Plug and Play and neighbours from the wireless controllers.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, Index, PrimaryKeyConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WirelessApStatus(Base):
    __tablename__ = "wireless_ap_status"
    __table_args__ = (Index("wireless_ap_status_controller", "controller"),)
    # No primary key in the table; the ORM needs an identity, the DDL gets none.
    __mapper_args__: ClassVar[dict] = {"primary_key": ["controller", "serial_key", "name_key"]}

    controller: Mapped[str] = mapped_column(Text, nullable=False)
    serial_key: Mapped[str] = mapped_column(Text, nullable=False)
    name_key: Mapped[str] = mapped_column(Text, nullable=False)
    serial: Mapped[str] = mapped_column(Text, nullable=False)
    mac: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    up: Mapped[bool] = mapped_column(Boolean, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class WirelessPnpStatus(Base):
    __tablename__ = "wireless_pnp_status"
    __table_args__ = (
        PrimaryKeyConstraint("controller", "serial_key", name="wireless_pnp_status_pkey"),
    )

    controller: Mapped[str] = mapped_column(Text)
    serial_key: Mapped[str] = mapped_column(Text)
    serial: Mapped[str] = mapped_column(Text, nullable=False)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    onb_state: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    last_contact: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WirelessApNeighbour(Base):
    __tablename__ = "wireless_ap_neighbours"
    __table_args__ = (Index("wireless_ap_neighbours_controller", "controller"),)
    # No primary key in the table; the ORM needs an identity, the DDL gets none.
    __mapper_args__: ClassVar[dict] = {"primary_key": ["controller", "serial_key", "name_key"]}

    controller: Mapped[str] = mapped_column(Text, nullable=False)
    serial_key: Mapped[str] = mapped_column(Text, nullable=False)
    name_key: Mapped[str] = mapped_column(Text, nullable=False)
    switch_name: Mapped[str] = mapped_column(Text, nullable=False)
    switch_port: Mapped[str] = mapped_column(Text, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
