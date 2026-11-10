"""Database engine and session factory. The per-request session is opened by
`app.db.middleware.DbSessionMiddleware`; controllers get it through the `get_db`
dependency (`DbSession`).
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.settings import get_settings

_settings = get_settings()


def connect_args(url: URL, connect_timeout: int, statement_timeout_seconds: int) -> dict:
    """Give up connecting after *connect_timeout* s and on any statement after
    *statement_timeout_seconds* (DB_CONNECT/STATEMENT_TIMEOUT_SECONDS, #190), as the
    current app's db.get_conn(): added to any options the URL has, not replacing them."""
    options = url.query.get("options") or ""
    if isinstance(options, tuple):
        options = " ".join(options)
    options = f"{options} -c statement_timeout={statement_timeout_seconds * 1000}".strip()
    return {"connect_timeout": connect_timeout, "options": options}


# create_engine does not connect; importing this module never touches the DB.
engine = create_engine(
    _settings.sqlalchemy_url(),
    echo=_settings.sql_echo,
    pool_pre_ping=True,
    connect_args=connect_args(
        _settings.sqlalchemy_url(),
        _settings.db_connect_timeout_seconds,
        _settings.db_statement_timeout_seconds,
    ),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
# The health check's own (#594): a new connection per probe (no stale pooled one, no
# waiting for a pool slot), 2 s to connect, 2 s for the statement, and a dead peer
# noticed within 2 s (tcp_user_timeout); app/services/health_service.py bounds the whole probe too.
probe_engine = create_engine(
    _settings.sqlalchemy_url(),
    poolclass=NullPool,
    connect_args={**connect_args(_settings.sqlalchemy_url(), 2, 2), "tcp_user_timeout": 2000},
)


def get_db(request: Request) -> Session:
    return request.state.db


DbSession = Annotated[Session, Depends(get_db)]
