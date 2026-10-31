"""Transactional state store backed by SQLAlchemy (PostgreSQL or SQLite).

Public API is identical to the original file-based store so that zero downstream
code (endpoints, scheduler, tests) needs to change:

    read() / load_state()   → deep copy of the current state dict
    transaction()           → context manager: yield a mutable copy; commit on exit
    save_state(st)          → persist state dict to DB
    reset() / reset_state() → atomically restore clean_snapshot (or a new dataset)
    fresh_state(data=None)  → build an initial state dict from cit_public.json
    audit(st, user, …)      → prepend an audit entry to st
    new_id(prefix)          → generate a prefixed random ID

Extra helpers for export persistence:
    save_export(filename, payload)  → persist to DB + disk
    load_exports_to_disk()          → sync DB exports → disk on container startup
    ensure_clean_snapshot()         → hydrate "clean_snapshot" row on first boot

Test isolation: ``conftest.py`` patches ``_state`` (the in-memory cache) and
``_session_factory`` (the SQLAlchemy session factory) so every test gets its own
fresh isolated SQLite database without touching global state.
"""
import copy
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .paths import DATASET, EXPORTS, STATE_DIR

# ── module-level vars (some are patchable by conftest.py for test isolation) ──
#
# STATE_FILE is kept as a module-level name for backward-compatibility with any
# code that references it; it is no longer used for persistence internally.
STATE_FILE = STATE_DIR / "state.json"

_lock = threading.RLock()
_state = None            # in-memory cache of the active state dict
_session_factory = None  # SQLAlchemy sessionmaker; patched in tests to an isolated factory

TEAM = os.getenv("TEAM_NAME", "Eternal Iterators")


# ── session helper ─────────────────────────────────────────────────────────────

def _session():
    """Return a new SQLAlchemy Session.

    Lazily initialises the real engine + tables on first call.
    Tests patch ``_session_factory`` before any call so they always get their
    own isolated SQLite session without hitting the real DB.
    """
    global _session_factory
    if _session_factory is None:
        from .database import SessionLocal, init_db
        init_db()
        _session_factory = SessionLocal
    return _session_factory()


# ── low-level DB read / write ──────────────────────────────────────────────────

def _db_read(row_id: str) -> dict | None:
    """Return the state dict stored under *row_id*, or ``None`` if absent."""
    from .database import AppState
    global _session_factory
    for attempt in range(2):
        try:
            with _session() as s:
                row = s.get(AppState, row_id)
                if row is None:
                    return None
                if isinstance(row.state_data, str):
                    return json.loads(row.state_data)
                return copy.deepcopy(row.state_data)
        except Exception:
            if attempt == 0:
                _session_factory = None
                time.sleep(0.5)
            else:
                raise


def _db_write(row_id: str, state_dict: dict) -> None:
    """Upsert the full state dict under *row_id*, with automatic reconnect retry."""
    from .database import AppState
    global _session_factory
    for attempt in range(2):
        try:
            with _session() as s:
                row = s.get(AppState, row_id)
                if row:
                    row.state_data = copy.deepcopy(state_dict)
                    row.updated_at = datetime.now(timezone.utc)
                else:
                    s.add(AppState(id=row_id, state_data=copy.deepcopy(state_dict)))
                s.commit()
            return
        except Exception:
            if attempt == 0:
                _session_factory = None
                time.sleep(0.5)
            else:
                raise


# ── public helpers ─────────────────────────────────────────────────────────────

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def fresh_state(data=None):
    if data is None:
        with open(DATASET, encoding="utf-8") as f:
            data = json.load(f)
    cancelled = {c["request_id"]: c for c in data.get("cancellations", [])}
    requests = []
    for r in data["booking_requests"]:
        rec = {
            "id": r["id"], "request": r, "submitted_by": None, "status": "pending",
            "placement": None, "reason": "Waiting for the Spectra-week allocation run.",
            "decided_by": None, "displaces": [], "overrides": [], "accepted": None,
        }
        if r["id"] in cancelled:
            c = cancelled[r["id"]]
            rec.update(
                status="cancelled",
                reason=f"Cancelled by {c['by']} on {c['cancelled_at'][:10]}: {c['note']}",
            )
        requests.append(rec)
    return {
        "original_data": data,        # as loaded (dataset file); disruptions re-applied on top
        "changes": [],                # every change applied since load, validator change format
        "data": copy.deepcopy(data),
        "timetable": None,            # sid -> [day, start, room]
        "generated_schedule": None,   # export snapshot right after generation (schedule.json)
        "exams": None,
        "requests": requests,
        "session_overrides": [],      # week-7 only moves caused by approved institute events
        "pending": [],                # staged timetable changes waiting for approval
        "history": [],
        "audit": [],
        "job": None,
    }


def _load() -> dict:
    """Return the active state, loading from DB if not yet cached."""
    global _state
    if _state is None:
        loaded = _db_read("active_state")
        if loaded is None:
            # First-ever boot: create and persist a fresh state
            _state = fresh_state()
            _db_write("active_state", _state)
        else:
            _state = loaded
    return _state


def read() -> dict:
    """Return a deep copy of the current state (safe to inspect without the lock)."""
    with _lock:
        return copy.deepcopy(_load())


def load_state() -> dict:
    """Alias for read() matching architectural specification."""
    return read()


def save_state(st: dict) -> None:
    """Directly persist *st* to active_state in the database and update cache."""
    global _state
    with _lock:
        _state = copy.deepcopy(st)
        _db_write("active_state", _state)


@contextmanager
def transaction():
    """Context manager: yields a mutable deep copy; commits to DB on success."""
    global _state
    with _lock:
        work = copy.deepcopy(_load())
        yield work
        _state = work
        _db_write("active_state", work)


def reset(data=None) -> None:
    """Atomically restore active_state from the clean snapshot (< 100 ms).

    If *data* is given (a new dataset dict), the snapshot itself is replaced first.
    """
    global _state
    with _lock:
        if data is not None:
            # New dataset supplied → rebuild and update the snapshot as well
            new_st = fresh_state(data)
            _db_write("clean_snapshot", new_st)
            _db_write("active_state", new_st)
            _state = new_st
        else:
            snapshot = _db_read("clean_snapshot")
            if snapshot is None:
                snapshot = fresh_state()
                _db_write("clean_snapshot", snapshot)
            _state = copy.deepcopy(snapshot)
            _db_write("active_state", _state)


def reset_state(data=None) -> None:
    """Alias for reset() matching architectural specification."""
    reset(data)


def ensure_clean_snapshot() -> None:
    """Hydrate the ``clean_snapshot`` and ``active_state`` rows on first startup.

    Called once from the FastAPI lifespan hook. Safe to call multiple times
    (no-op if the rows already exist). Pre-populates the verified baseline schedule
    from exports/schedule.json so the initial timetable is ready without a cold solve.
    """
    if _db_read("clean_snapshot") is None:
        snap = fresh_state()
        sched_file = EXPORTS / "schedule.json"
        if sched_file.exists():
            try:
                saved = json.loads(sched_file.read_text(encoding="utf-8"))
                if saved.get("sessions"):
                    snap["timetable"] = {s["session_id"]: [s["day"], s["start_period"], s["room_id"]] for s in saved["sessions"]}
                    snap["generated_schedule"] = saved
                    if saved.get("exams"):
                        snap["exams"] = saved["exams"]
            except Exception:
                pass
        _db_write("clean_snapshot", snap)
    if _db_read("active_state") is None:
        snap = _db_read("clean_snapshot") or fresh_state()
        _db_write("active_state", copy.deepcopy(snap))


# ── export persistence ─────────────────────────────────────────────────────────

def save_export(filename: str, payload: dict) -> None:
    """Persist *payload* under *filename* in the DB **and** write it to disk.

    Both sinks are kept in sync so that:
    - ``validator.py`` CLI and ``GET /api/export/schedule`` see the file on disk.
    - A container restart can restore the file from the DB via ``load_exports_to_disk()``.
    """
    from .database import ScheduleExport
    global _session_factory
    for attempt in range(2):
        try:
            with _session() as s:
                row = s.get(ScheduleExport, filename)
                if row:
                    row.payload = copy.deepcopy(payload)
                    row.updated_at = datetime.now(timezone.utc)
                else:
                    s.add(ScheduleExport(filename=filename, payload=copy.deepcopy(payload)))
                s.commit()
            break
        except Exception:
            if attempt == 0:
                _session_factory = None
                time.sleep(0.5)
            else:
                pass  # Non-fatal if DB write fails, disk write below will still succeed
    # Also write to disk for backward compatibility
    EXPORTS.mkdir(parents=True, exist_ok=True)
    data_str = json.dumps(payload, indent=2)
    (EXPORTS / filename).write_text(data_str, encoding="utf-8")


def load_exports_to_disk() -> None:
    """Sync persisted exports from DB → disk on container startup.

    Non-fatal: silently no-ops if the table is empty (first boot, pre-generation).
    """
    from .database import ScheduleExport
    try:
        with _session() as s:
            rows = s.query(ScheduleExport).all()
        if rows:
            EXPORTS.mkdir(parents=True, exist_ok=True)
            for row in rows:
                content = json.dumps(row.payload, indent=2) if not isinstance(row.payload, str) else row.payload
                (EXPORTS / row.filename).write_text(content, encoding="utf-8")
    except Exception:
        pass  # Non-fatal on first boot


def audit(st, user, action, detail):
    st["audit"].insert(0, {"at": now(), "user": user, "action": action, "detail": detail})
    del st["audit"][300:]


# ── read-only helpers (unchanged from original) ────────────────────────────────

def assign_of(st):
    return {sid: tuple(v) for sid, v in (st["timetable"] or {}).items()}


def week_assign(st):
    """Week-7 view: base timetable with Spectra overrides applied."""
    a = assign_of(st)
    for o in st["session_overrides"]:
        a[o["session_id"]] = (o["day"], o["start_period"], o["room_id"])
    return a


def placed_bookings(st, ignore=()):
    """Bookings that hold a room in Week 7 (approved or alternative offered)."""
    out = []
    for r in st["requests"]:
        if r["id"] in ignore or r["status"] not in ("approved", "alternative_offered") or not r["placement"]:
            continue
        out.append(dict(r["placement"], request_id=r["id"],
                        mandatory_for_groups=r["request"].get("mandatory_for_groups", [])))
    return out


def export_schedule(st):
    out = {
        "team": TEAM,
        "dataset_id": st["data"].get("dataset_id"),
        "sessions": [
            {"session_id": sid, "day": d, "start_period": t, "room_id": r}
            for sid, (d, t, r) in sorted((st["timetable"] or {}).items())
        ],
    }
    bookings = []
    for r in st["requests"]:
        if r["status"] == "pending":
            continue
        b = {
            "request_id": r["id"],
            "status": "rejected" if r["status"] == "cancelled" else r["status"],
            "reason": r["reason"],
        }
        if r["status"] in ("approved", "alternative_offered") and r["placement"]:
            b.update(r["placement"])
        bookings.append(b)
    out["fest_week"] = {
        "bookings": bookings,
        "session_overrides": [
            {k: o[k] for k in ("session_id", "day", "start_period", "room_id", "displaced_by")}
            for o in st["session_overrides"]
        ],
    }
    if st["exams"]:
        out["exams"] = st["exams"]
    return out
