"""Database engine, session management, and WAL mode configuration.

Initialises the SQLite engine in Write-Ahead Logging (WAL) mode for
safer concurrent access and clean backups.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel

DATABASE_URL = "sqlite:///./data/invoices.db"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Enable WAL mode for safer concurrent access and backups
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    """Set SQLite journal mode to WAL on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session)


def create_db_and_tables():
    """Create all SQLModel tables if they don't already exist."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """Yield a new database session, closing it on exit.

    Usage::

        with get_session() as session:
            ...  # use session

    In FastAPI, use ``Depends(get_session)`` for dependency injection.
    """
    with SessionLocal() as session:
        yield session
