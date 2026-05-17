"""
Tests for the database layer (WAL mode, table creation).
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import engine, create_db_and_tables, get_session  # noqa: E402
from src.models import Client, Invoice, TimeLog  # noqa: E402


class TestWALMode:
    """Verify SQLite WAL mode initialization."""

    def test_wal_mode_on_engine_connect(self):
        """PRAGMA journal_mode returns 'wal' for the production engine."""
        sess = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
        try:
            result = sess.execute(text("PRAGMA journal_mode")).scalar()
            assert result == "wal", f"Expected 'wal', got '{result}'"
        finally:
            sess.close()

    def test_wal_mode_on_fresh_engine(self):
        """A fresh engine with WAL listener also gets WAL mode."""
        import tempfile
        # WAL mode requires a file; in-memory SQLite falls back to 'memory'
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            test_engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False},
            )

            @event.listens_for(test_engine, "connect")
            def set_wal(dbapi_conn, _):
                dbapi_conn.cursor().execute("PRAGMA journal_mode=WAL;")
                dbapi_conn.cursor().close()

            # Create a session to trigger the connect event
            test_sess = sessionmaker(bind=test_engine)()
            # Execute a query to ensure the connection is established
            # and the WAL mode is set
            result = test_sess.execute(text("PRAGMA journal_mode")).scalar()
            # The WAL mode should be set by the event listener
            assert result == "wal", f"Expected 'wal', got '{result}'"
            test_sess.close()
        finally:
            import os
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestTableCreation:
    """Verify create_db_and_tables() creates all expected tables."""

    def test_create_db_and_tables_runs(self):
        """create_db_and_tables() executes without error."""
        # It should be safe to call multiple times
        create_db_and_tables()
        create_db_and_tables()

    def test_all_three_tables_exist(self):
        """Client, timelog, and invoice tables are created."""
        create_db_and_tables()
        from sqlalchemy import inspect
        insp = inspect(engine)
        tables = {t.lower() for t in insp.get_table_names()}
        assert "client" in tables
        assert "timelog" in tables
        assert "invoice" in tables

    def test_invoice_number_has_unique_constraint(self):
        """Invoice.invoice_number has a unique constraint enforced at DB level."""
        create_db_and_tables()
        sess = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
        try:
            from sqlmodel import Session as SqlModelSession
            sm = SqlModelSession(engine)
            sm.add(Client(
                name="C1", email="c1@test.com",
                billing_address="Addr1", default_hourly_rate=50.00,
            ))
            sm.commit()
            c1_id = sm.get(Client, 1).id
            sm.add(TimeLog(
                date=__import__("datetime").date.today(),
                hours=1.0, description="test",
                client_id=c1_id,
            ))
            sm.commit()
            t1_id = sm.get(TimeLog, 1).id

            inv1 = Invoice(invoice_number="INV-2025-0001",
                           due_date=__import__("datetime").date.today(),
                           total_amount=100.00, client_id=c1_id)
            sm.add(inv1)
            sm.flush()

            with pytest.raises(Exception):  # IntegrityError / OperationalError
                inv2 = Invoice(invoice_number="INV-2025-0001",
                               due_date=__import__("datetime").date.today(),
                               total_amount=200.00, client_id=c1_id)
                sm.add(inv2)
                sm.commit()
        finally:
            sm.close()

    def test_session_getter_yields_session(self):
        """get_session() is a generator that yields a valid session."""
        gen = get_session()
        sess = next(gen)
        assert sess is not None
        # Should be able to execute a query
        result = sess.execute(text("SELECT 1")).scalar()
        assert result == 1
        # Generator should close the session after use
        try:
            next(gen)
            assert False, "Generator should not yield twice"
        except StopIteration:
            pass
