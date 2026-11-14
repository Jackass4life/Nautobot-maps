"""Roles, their scopes, and identities (#713): the example's models (example/auth/
models), on this app's Base. A scope (``alerts:ack``, ``alerts:*``) belongs to a role
or to one identity; an identity has roles. Identities are used from the next part
of the auth work on; until then their tables stay empty."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    # Ours, not the example's: what the pages call it, and the page it opens on.
    label: Mapped[str] = mapped_column(String(255), server_default="")
    landing: Mapped[str] = mapped_column(String(255), server_default="/")
    # One of the five the app starts with: edited, never deleted.
    built_in: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Identity(Base):
    __tablename__ = "identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The user name a sign-in gives (the proxy's, or AD's), lowercase.
    unique_name: Mapped[str] = mapped_column(String, unique=True)
    common_name: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentitiesToRoles(Base):
    __tablename__ = "identities_to_roles"
    __table_args__ = (UniqueConstraint("identity_id", "role_id", name="unique_role_assignments"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="CASCADE"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("scope", "identity_id", "role_id", name="unique_permission_assignments"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(255))
    identity_id: Mapped[int | None] = mapped_column(ForeignKey("identities.id", ondelete="CASCADE"))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
