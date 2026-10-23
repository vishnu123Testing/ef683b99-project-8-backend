"""Database connection engine, session factory, and declarative base metadata."""

from typing import Any, Dict, Generator
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.lib.config import settings


def get_engine_args(url: str) -> Dict[str, Any]:
    """Return kwargs for create_engine based on database URL protocol."""
    engine_args: Dict[str, Any] = {}
    if url.startswith("sqlite"):
        engine_args["connect_args"] = {"check_same_thread": False}
    else:
        engine_args["pool_pre_ping"] = True
    return engine_args


def create_db_engine(url: str | None = None) -> Engine:
    """Create a synchronous SQLAlchemy engine for the given or default database URL."""
    db_url = url if url is not None else settings.database_url
    return create_engine(db_url, **get_engine_args(db_url))


engine = create_db_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Base declarative metadata class for SQLAlchemy ORM models."""

    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
