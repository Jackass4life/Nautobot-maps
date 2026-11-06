"""API tokens and tenant status page links.

Today's schema (version 33 of nautobot_maps/db.py), generated from it (#590):
names, types, defaults, constraints and indexes as they are, so an existing database
is adopted as is. tests/test_schema.py checks it."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, PrimaryKeyConstraint, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="api_tokens_pkey"),
        UniqueConstraint("name", name="api_tokens_name_key"),
        UniqueConstraint("token_hash", name="api_tokens_token_hash_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    tenant: Mapped[str | None] = mapped_column(Text)


class StatusLink(Base):
    __tablename__ = "status_links"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_key", name="status_links_pkey"),
        UniqueConstraint("link_id", name="status_links_link_id_key"),
    )

    tenant_key: Mapped[str] = mapped_column(Text)
    link_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
