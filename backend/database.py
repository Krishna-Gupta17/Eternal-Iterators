"""SQLAlchemy database engine, session factory, and ORM models.

Dual-engine support:
  • PostgreSQL (Neon / Supabase / Render Postgres) when DATABASE_URL is set to a
    ``postgresql://`` or ``postgres://`` URI.
  • SQLite with WAL mode when DATABASE_URL is unset or starts with ``sqlite:///``
    (local dev, CI, offline testing — zero external setup required).
  • Resilient auto-fallback: if a remote PostgreSQL database is unreachable
    (e.g. cold start, network firewall blocking port 5432, offline test), the app
    gracefully falls back to local SQLite with WAL mode so the container starts
    cleanly without crashing.

Test isolation: ``conftest.py`` patches ``store._session_factory`` so every test
gets its own fresh SQLite database in a tmp directory.
"""
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, DateTime, JSON, String, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger("cit_scheduler.database")

# ── helpers for SQLite configuration ──────────────────────────────────────────

def get_default_sqlite_path() -> Path:
    base = Path(__file__).resolve().parent.parent / "backend" / "state" / "cit_scheduler.db"
    base.parent.mkdir(parents=True, exist_ok=True)
    return base


def create_sqlite_engine(url: str):
    eng = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        """Enable WAL mode for better concurrency and crash safety."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return eng


# ── resolve connection URL ─────────────────────────────────────────────────────
def clean_database_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return f"sqlite:///{get_default_sqlite_path()}"

    # Remove channel_binding parameter which breaks connection poolers like PgBouncer / Neon
    raw = re.sub(r"[?&]channel_binding=[^&]*", "", raw)
    if "?" not in raw and "&" in raw:
        raw = raw.replace("&", "?", 1)

    if raw.startswith("postgres://"):
        return "postgresql+psycopg2://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://") and "+psycopg2" not in raw:
        return "postgresql+psycopg2://" + raw[len("postgresql://"):]
    return raw


DATABASE_URL: str = clean_database_url(os.getenv("DATABASE_URL", ""))

# ── create engine ─────────────────────────────────────────────────────────────
if DATABASE_URL.startswith("sqlite"):
    engine = create_sqlite_engine(DATABASE_URL)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # keeps cloud Postgres connections alive across idle periods
        pool_recycle=30,     # recycle connections older than 30s to prevent stale connections during long solver runs
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": 8},
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ── ORM models ─────────────────────────────────────────────────────────────────
StateJSON = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class AppState(Base):
    """Stores the complete application state as a serialized JSON / JSONB document.

    Two rows are maintained:
    - ``id="active_state"``   — the live working state (timetable, bookings, approvals …)
    - ``id="clean_snapshot"`` — pristine initial state; copied to active on Admin Reset
    """

    __tablename__ = "app_state"

    id = Column(String, primary_key=True)      # "active_state" | "clean_snapshot"
    state_data = Column(StateJSON, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ScheduleExport(Base):
    """Persists generated schedule exports so they survive container restarts.

    On startup, rows are synced back to ``exports/`` on disk so that
    ``validator.py`` and ``GET /api/export/schedule`` always see the latest data.
    """

    __tablename__ = "schedule_exports"

    filename = Column(String, primary_key=True)  # "schedule.json" | "schedule_after.json"
    payload = Column(StateJSON, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def init_db(target_engine=None) -> None:
    """Create tables if they don't exist. Idempotent — safe to call on every startup.

    If a configured PostgreSQL database is unreachable (e.g. cold start, network
    firewall blocking port 5432, offline test), gracefully falls back to local
    SQLite with WAL mode so the container starts cleanly and remains functional.
    """
    global engine, DATABASE_URL
    eng = target_engine or engine

    # If configured with PostgreSQL, test connectivity before proceeding
    if not str(eng.url).startswith("sqlite"):
        connected = False
        last_error = None
        for attempt in range(2):
            try:
                with eng.connect() as conn:
                    conn.execute(text("SELECT 1"))
                connected = True
                break
            except Exception as e:
                last_error = e
                time.sleep(1)

        if not connected:
            sqlite_path = get_default_sqlite_path()
            fallback_url = f"sqlite:///{sqlite_path}"
            print(
                f"\n[WARNING] Could not connect to remote PostgreSQL at {eng.url.host}: {last_error}\n"
                f"[WARNING] Transparently falling back to local SQLite with WAL mode ({sqlite_path}).\n"
                f"[WARNING] The application is fully functional and ready to serve requests.\n",
                flush=True,
            )
            DATABASE_URL = fallback_url
            engine = create_sqlite_engine(fallback_url)
            SessionLocal.configure(bind=engine)
            eng = engine

    Base.metadata.create_all(bind=eng)
