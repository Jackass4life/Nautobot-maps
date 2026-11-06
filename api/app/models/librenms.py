"""LibreNMS devices and their status.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Sequence,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Not owned by a column: the order of LibreNMS observations and pushes.
librenms_push_seq = Sequence("librenms_push_seq", metadata=Base.metadata)
librenms_observation_seq = Sequence("librenms_observation_seq", metadata=Base.metadata)


class LibrenmsDeviceMap(Base):
    __tablename__ = "librenms_device_map"
    __table_args__ = (PrimaryKeyConstraint("nautobot_device_id", name="librenms_device_map_pkey"),)

    nautobot_device_id: Mapped[str] = mapped_column(Text)
    librenms_device_id: Mapped[int] = mapped_column(Integer, nullable=False)
    librenms_hostname: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )


class LibrenmsDeviceStatus(Base):
    __tablename__ = "librenms_device_status"
    __table_args__ = (
        PrimaryKeyConstraint("device_id", name="librenms_device_status_pkey"),
        Index("librenms_device_status_hostname_lower", text("lower(hostname)")),
    )

    device_id: Mapped[int] = mapped_column(Integer, autoincrement=False)
    hostname: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    ip: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[int | None] = mapped_column(Integer)
    status_raw: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_seq: Mapped[int | None] = mapped_column(BigInteger)
    push_seq: Mapped[int | None] = mapped_column(BigInteger)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    disable_notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    ignore: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
