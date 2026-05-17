"""
Shared pytest fixtures for Lean Invoice Tracker tests.

Provides an in-memory SQLite database that mirrors the production
sqlite:///./data/invoices.db setup, with WAL mode enabled.
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel

# Ensure the project root is on sys.path so `src` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models import Client, Invoice, InvoiceSequence, TimeLog  # noqa: E402


# ── Test database engine (in-memory, WAL mode) ──────────────────────────────

TEST_DATABASE_URL = "sqlite:///:memory:"


def _create_test_engine():
    """Create an in-memory SQLite engine with WAL mode, mirroring production."""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_wal_mode(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()

    return engine


_test_engine = _create_test_engine()
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


@pytest.fixture(scope="session")
def test_engine():
    """Session-scoped test engine (in-memory SQLite with WAL)."""
    yield _test_engine
    SQLModel.metadata.drop_all(_test_engine)


@pytest.fixture()
def session(test_engine):
    """
    Per-test database session with a fresh in-memory database.
    Drops and recreates all tables before each test to ensure clean state.
    Uses a regular SQLAlchemy Session (which has .exec() via SQLModel).
    """
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)
    # Use Session (SQLAlchemy) which supports .exec() through SQLModel patching
    sess = Session(test_engine)
    try:
        yield sess
    finally:
        sess.close()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def client_obj(session):
    """A client with a 100.00/hr rate."""
    c = Client(
        name="Test Client",
        email="test@example.com",
        billing_address="123 Test St",
        default_hourly_rate=100.00,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@pytest.fixture
def client_high_rate(session):
    """A client with a 150.50/hr rate."""
    c = Client(
        name="Premium Client",
        email="premium@example.com",
        billing_address="456 Premium Ave",
        default_hourly_rate=150.50,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@pytest.fixture
def unbilled_logs(session, client_obj):
    """Create 5 unbilled time logs for the default client."""
    today = datetime.date.today()
    logs = [
        TimeLog(date=today - datetime.timedelta(days=4), hours=10.0, description="Log A", client_id=client_obj.id),
        TimeLog(date=today - datetime.timedelta(days=3), hours=5.0, description="Log B", client_id=client_obj.id),
        TimeLog(date=today - datetime.timedelta(days=2), hours=3.0, description="Log C", client_id=client_obj.id),
        TimeLog(date=today - datetime.timedelta(days=1), hours=2.0, description="Log D", client_id=client_obj.id),
        TimeLog(date=today, hours=5.0, description="Log E", client_id=client_obj.id),
    ]
    session.add_all(logs)
    session.commit()
    return logs


@pytest.fixture
def mixed_logs(session, client_obj, client_high_rate):
    """Create a mix of billed and unbilled logs across 2 clients."""
    today = datetime.date.today()
    # Client 1: 3 unbilled, 2 billed
    logs_c1 = [
        TimeLog(date=today - datetime.timedelta(days=3), hours=4.0, description="C1-unbilled-1",
                client_id=client_obj.id, is_billed=False),
        TimeLog(date=today - datetime.timedelta(days=2), hours=2.5, description="C1-unbilled-2",
                client_id=client_obj.id, is_billed=False),
        TimeLog(date=today - datetime.timedelta(days=1), hours=1.0, description="C1-unbilled-3",
                client_id=client_obj.id, is_billed=False),
        TimeLog(date=today - datetime.timedelta(days=5), hours=3.0, description="C1-billed-1",
                client_id=client_obj.id, is_billed=True, invoice_id=1),
        TimeLog(date=today - datetime.timedelta(days=6), hours=2.0, description="C1-billed-2",
                client_id=client_obj.id, is_billed=True, invoice_id=1),
    ]
    # Client 2: 2 unbilled
    logs_c2 = [
        TimeLog(date=today - datetime.timedelta(days=2), hours=6.0, description="C2-unbilled-1",
                client_id=client_high_rate.id, is_billed=False),
        TimeLog(date=today - datetime.timedelta(days=1), hours=3.5, description="C2-unbilled-2",
                client_id=client_high_rate.id, is_billed=False),
    ]
    session.add_all(logs_c1 + logs_c2)
    session.commit()
    return logs_c1 + logs_c2
