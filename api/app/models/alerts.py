"""Alerts, their events and cases, site levels, device states, notifications.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AlertInstance(Base):
    __tablename__ = "alert_instances"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="alert_instances_pkey"),
        Index("idx_alert_instances_key", "alert_key"),
        Index(
            "idx_alert_instances_period",
            text("COALESCE(resolved_at, 'infinity'::timestamp with time zone)"),
            "down_started_at",
        ),
        Index("idx_alert_instances_site_status", "site_id", "status"),
        Index(
            "uq_alert_instances_open_key",
            "alert_key",
            unique=True,
            postgresql_where=text("(status = 'open'::text)"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    alert_key: Mapped[str] = mapped_column(Text, nullable=False)
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    site_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    device_id: Mapped[str] = mapped_column(Text, nullable=False)
    device_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    alert_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'::text")
    )
    alert_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'::text"))
    down_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_down_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_downtime_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    probable_cause: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    is_core: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    ack_note: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    access_point: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="alert_events_pkey"),
        ForeignKeyConstraint(
            ["alert_instance_id"],
            ["alert_instances.id"],
            name="alert_events_alert_instance_id_fkey",
            ondelete="CASCADE",
        ),
        Index("idx_alert_events_instance_time", "alert_instance_id", "event_at"),
        Index("idx_alert_events_time", "event_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    alert_instance_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    alert_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'::text")
    )
    alert_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'{}'::text")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AlertCase(Base):
    __tablename__ = "alert_cases"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="alert_cases_pkey"),
        UniqueConstraint(
            "alert_instance_id", "case_number", name="alert_cases_alert_instance_id_case_number_key"
        ),
        ForeignKeyConstraint(
            ["alert_instance_id"],
            ["alert_instances.id"],
            name="alert_cases_alert_instance_id_fkey",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    alert_instance_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    case_number: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class SiteAlertLevel(Base):
    __tablename__ = "site_alert_levels"
    __table_args__ = (PrimaryKeyConstraint("site_id", name="site_alert_levels_pkey"),)

    site_id: Mapped[str] = mapped_column(Text)
    site_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    alert_level: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criticality_key: Mapped[str | None] = mapped_column(Text)
    down_key: Mapped[str | None] = mapped_column(Text)


class SiteLevelChange(Base):
    __tablename__ = "site_level_changes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="site_level_changes_pkey"),
        Index("idx_site_level_changes_site_time", "site_id", "changed_at"),
        Index("idx_site_level_changes_time", "changed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    site_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    from_level: Mapped[str] = mapped_column(Text, nullable=False)
    to_level: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceState(Base):
    __tablename__ = "device_states"
    __table_args__ = (PrimaryKeyConstraint("device_id", name="device_states_pkey"),)

    device_id: Mapped[str] = mapped_column(Text)
    site_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    state: Mapped[str] = mapped_column(Text, nullable=False)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changes: Mapped[list[datetime]] = mapped_column(
        ARRAY(DateTime(timezone=True)),
        nullable=False,
        server_default=text("'{}'::timestamp with time zone[]"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    flapping_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))


class BoardBuildStat(Base):
    __tablename__ = "board_build_stats"
    __table_args__ = (PrimaryKeyConstraint("trigger", name="board_build_stats_pkey"),)

    trigger: Mapped[str] = mapped_column(Text)
    builds: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    total_seconds: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
    last_seconds: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
    last_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDeviceAlert(Base):
    __tablename__ = "webhook_device_alerts"
    __table_args__ = (
        PrimaryKeyConstraint("source", "alert_key", name="webhook_device_alerts_pkey"),
        Index("webhook_device_alerts_updated", "updated_at"),
    )

    source: Mapped[str] = mapped_column(Text)
    alert_key: Mapped[str] = mapped_column(Text)
    device_key: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="notification_outbox_pkey"),
        Index("notification_outbox_due", "channel", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    site_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'::text")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceCriticalityOverride(Base):
    __tablename__ = "device_criticality_override"
    __table_args__ = (
        PrimaryKeyConstraint("nautobot_device_id", name="device_criticality_override_pkey"),
    )

    nautobot_device_id: Mapped[str] = mapped_column(Text)
    is_critical: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
