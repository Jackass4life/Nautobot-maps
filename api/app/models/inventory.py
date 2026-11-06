"""The PostgreSQL snapshot of Nautobot (inventory sync) and the data contract checks.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NautobotLocationCache(Base):
    __tablename__ = "nautobot_location_cache"
    __table_args__ = (
        PrimaryKeyConstraint("location_id", name="nautobot_location_cache_pkey"),
        Index("nautobot_location_cache_address_key", "address_key"),
    )

    location_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    slug: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    location_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    parent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    parent_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    physical_address: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    facility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant_group: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    asn: Mapped[int | None] = mapped_column(BigInteger)
    time_zone: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'::text"))
    url: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    country: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tier: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    address_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant_scope_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'{}'::text")
    )


class NautobotDeviceCache(Base):
    __tablename__ = "nautobot_device_cache"
    __table_args__ = (
        PrimaryKeyConstraint("device_id", name="nautobot_device_cache_pkey"),
        Index("idx_nautobot_device_cache_location", "location_id"),
    )

    device_id: Mapped[str] = mapped_column(Text)
    location_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    device_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    manufacturer: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    primary_ip: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    platform: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    serial: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    criticality: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    vc_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    vc_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    vc_position: Mapped[int | None] = mapped_column(Integer)
    vc_master: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class NautobotTenantCache(Base):
    __tablename__ = "nautobot_tenant_cache"
    __table_args__ = (PrimaryKeyConstraint("tenant_id", name="nautobot_tenant_cache_pkey"),)

    tenant_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class NautobotLocationTenantCache(Base):
    __tablename__ = "nautobot_location_tenant_cache"
    __table_args__ = (
        PrimaryKeyConstraint(
            "location_id", "tenant_id", "relationship", name="nautobot_location_tenant_cache_pkey"
        ),
    )

    location_id: Mapped[str] = mapped_column(Text)
    tenant_id: Mapped[str] = mapped_column(Text)
    tenant: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    relationship: Mapped[str] = mapped_column(Text, server_default=text("''::text"))


class NautobotCircuitCache(Base):
    __tablename__ = "nautobot_circuit_cache"
    __table_args__ = (
        PrimaryKeyConstraint("circuit_id", name="nautobot_circuit_cache_pkey"),
        Index("nautobot_circuit_cache_address_key", "address_key"),
        Index("nautobot_circuit_cache_tenant_key", "tenant_key"),
    )

    circuit_id: Mapped[str] = mapped_column(Text)
    cid: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    circuit_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    commit_rate: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    address_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    terminated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    location_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    tenant_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class NautobotCableCache(Base):
    __tablename__ = "nautobot_cable_cache"
    __table_args__ = (
        PrimaryKeyConstraint("cable_id", name="nautobot_cable_cache_pkey"),
        Index("nautobot_cable_cache_a", "a_device_id"),
        Index("nautobot_cable_cache_b", "b_device_id"),
    )

    cable_id: Mapped[str] = mapped_column(Text)
    a_device_id: Mapped[str] = mapped_column(Text, nullable=False)
    a_port: Mapped[str] = mapped_column(Text, nullable=False)
    b_device_id: Mapped[str] = mapped_column(Text, nullable=False)
    b_port: Mapped[str] = mapped_column(Text, nullable=False)


class InventorySyncState(Base):
    __tablename__ = "inventory_sync_state"
    __table_args__ = (PrimaryKeyConstraint("source", name="inventory_sync_state_pkey"),)

    source: Mapped[str] = mapped_column(Text)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cache_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'idle'::text"))
    error_message: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContractCheck(Base):
    __tablename__ = "contract_checks"
    __table_args__ = (
        Index("contract_checks_row", "source", "endpoint", "field", "problem", unique=True),
    )
    # No primary key in the table; the ORM needs an identity, the DDL gets none.
    __mapper_args__: ClassVar[dict] = {"primary_key": ["source", "endpoint", "field", "problem"]}

    source: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    problem: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    mismatched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
