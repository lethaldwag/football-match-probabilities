"""Database engine/session helpers and shared column types."""

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime that behaves the same on PostgreSQL and SQLite.

    Naive datetimes are rejected so a local time can never be stored by accident.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # SQLite does not enforce foreign keys by default
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_sessionmaker() -> sessionmaker[Session]:
    global _engine, _factory
    if _factory is None:
        _engine = make_engine()
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with get_sessionmaker()() as session:
        yield session
