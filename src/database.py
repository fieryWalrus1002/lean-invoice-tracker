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
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with SessionLocal() as session:
        yield session
