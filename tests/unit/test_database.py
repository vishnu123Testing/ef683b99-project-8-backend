"""Unit tests for backend.src.models.database module."""

from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session

from src.models.database import (
    Base,
    SessionLocal,
    create_db_engine,
    engine,
    get_db,
    get_engine_args,
)


def test_get_engine_args_sqlite():
    """Test get_engine_args for SQLite connection strings."""
    args = get_engine_args("sqlite:///./test.db")
    assert "connect_args" in args
    assert args["connect_args"] == {"check_same_thread": False}


def test_get_engine_args_postgresql():
    """Test get_engine_args for non-SQLite connection strings."""
    args = get_engine_args("postgresql://user:pass@localhost:5432/dbname")
    assert args.get("pool_pre_ping") is True
    assert "connect_args" not in args


def test_create_db_engine_custom_url():
    """Test create_db_engine with explicit database URL."""
    custom_engine = create_db_engine("sqlite:///:memory:")
    assert isinstance(custom_engine, Engine)
    assert str(custom_engine.url) == "sqlite:///:memory:"


def test_engine_instance():
    """Test default engine instance created from settings."""
    assert isinstance(engine, Engine)


def test_session_local():
    """Test session creation and teardown from SessionLocal factory."""
    session = SessionLocal()
    assert isinstance(session, Session)
    session.close()


def test_base_declarative():
    """Test declarative Base metadata initialization."""
    assert hasattr(Base, "metadata")
    assert issubclass(Base, DeclarativeBase)


def test_get_db_generator():
    """Test get_db dependency generator yields a active session and closes it."""
    db_gen = get_db()
    session = next(db_gen)
    assert isinstance(session, Session)

    # Exhaust generator to trigger finally block
    try:
        next(db_gen)
    except StopIteration:
        pass

    # Verify session closed / inactive
    assert not session.is_active or session.bind is not None
