"""Transactional state store with PostgreSQL and JSON-file backends.

All mutations go through `transaction()`: work on a deep copy and commit only if the
block finishes without raising. PostgreSQL stores the same state document as JSONB
and locks its row for the duration of a mutation.
"""
import copy
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .paths import DATASET, STATE_DIR

STATE_FILE = STATE_DIR / "state.json"
TEAM = os.getenv("TEAM_NAME", "Eternal Iterators")
_lock = threading.RLock()
_state = None


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
        rec = {"id": r["id"], "request": r, "submitted_by": None, "status": "pending",
               "placement": None, "reason": "Waiting for the Spectra-week allocation run.",
               "decided_by": None, "displaces": [], "overrides": [], "accepted": None}
        if r["id"] in cancelled:
            c = cancelled[r["id"]]
            rec.update(status="cancelled", reason=f"Cancelled by {c['by']} on {c['cancelled_at'][:10]}: {c['note']}")
        requests.append(rec)
    return {
        "original_data": data,       # as loaded (dataset file); disruptions are re-applied on top
        "changes": [],               # every change applied since load, validator change format
        "data": copy.deepcopy(data),
        "timetable": None,           # sid -> [day, start, room]
        "generated_schedule": None,  # export snapshot right after generation (schedule.json)
        "exams": None,
        "requests": requests,
        "session_overrides": [],     # week-7 only moves caused by approved institute events
        "pending": [],               # staged timetable changes waiting for approval
        "history": [],
        "audit": [],
        "job": None,
    }


def _load():
    global _state
    if _state is None:
        if STATE_FILE.exists():
            with open(STATE_FILE, encoding="utf-8") as f:
                _state = json.load(f)
        else:
            _state = fresh_state()
            _save(_state)
    return _state


def _save(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def _database_url():
    return os.getenv("DATABASE_URL", "").strip()


def _pg_connect():
    import psycopg

    return psycopg.connect(_database_url())


def _pg_ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduler_state (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            state JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _pg_state(conn, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = conn.execute(f"SELECT state FROM scheduler_state WHERE id = 1{suffix}").fetchone()
    if row is None:
        state = fresh_state()
        from psycopg.types.json import Jsonb

        conn.execute(
            "INSERT INTO scheduler_state (id, state) VALUES (1, %s) ON CONFLICT (id) DO NOTHING",
            (Jsonb(state),),
        )
        row = conn.execute(f"SELECT state FROM scheduler_state WHERE id = 1{suffix}").fetchone()
    return row[0]


def view():
    """The current state without copying, for read-only paths (GET handlers). Callers
    must not mutate it. Safe without the lock: a commit swaps in a new state object
    instead of editing the live one, so a view is a consistent snapshot."""
    if _database_url():
        with _pg_connect() as conn:
            _pg_ensure_table(conn)
            return _pg_state(conn)          # freshly decoded from JSONB: already private
    with _lock:
        return _load()


def read():
    """A deep copy of the current state, for callers that mutate it (e.g. solver jobs)."""
    if _database_url():
        return view()
    with _lock:
        return copy.deepcopy(_load())


@contextmanager
def transaction():
    global _state
    if _database_url():
        with _lock:
            with _pg_connect() as conn:
                _pg_ensure_table(conn)
                work = copy.deepcopy(_pg_state(conn, lock=True))
                yield work
                from psycopg.types.json import Jsonb

                conn.execute(
                    "UPDATE scheduler_state SET state = %s, updated_at = NOW() WHERE id = 1",
                    (Jsonb(work),),
                )
        return
    with _lock:
        work = copy.deepcopy(_load())
        yield work
        _state = work
        _save(work)


def reset(data=None):
    global _state
    if _database_url():
        state = fresh_state(data)
        from psycopg.types.json import Jsonb

        with _lock:
            with _pg_connect() as conn:
                _pg_ensure_table(conn)
                conn.execute(
                    """
                    INSERT INTO scheduler_state (id, state) VALUES (1, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET state = EXCLUDED.state, updated_at = NOW()
                    """,
                    (Jsonb(state),),
                )
        return
    with _lock:
        _state = fresh_state(data)
        _save(_state)


def import_state_file(path=STATE_FILE):
    """Import an existing JSON state file into an empty PostgreSQL state store."""
    if not _database_url():
        raise RuntimeError("Set DATABASE_URL before importing state into PostgreSQL")
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    from psycopg.types.json import Jsonb

    with _lock:
        with _pg_connect() as conn:
            _pg_ensure_table(conn)
            row = conn.execute("SELECT 1 FROM scheduler_state WHERE id = 1 FOR UPDATE").fetchone()
            if row:
                raise RuntimeError("PostgreSQL already contains scheduler state; refusing to overwrite it")
            conn.execute(
                "INSERT INTO scheduler_state (id, state) VALUES (1, %s)",
                (Jsonb(state),),
            )


def sync_state_file(path=STATE_FILE):
    """Back up PostgreSQL state, then replace it with a validated local state file."""
    if not _database_url():
        raise RuntimeError("Set DATABASE_URL before syncing state to PostgreSQL")
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or not all(k in state for k in ("data", "timetable", "requests", "audit")):
        raise ValueError("The state file is missing required scheduler data")
    if not isinstance(state["data"], dict) or not isinstance(state["requests"], list):
        raise ValueError("The state file has an invalid scheduler data structure")
    if not isinstance(state["timetable"], dict) or not state["timetable"]:
        raise ValueError("The local state does not contain a generated timetable")

    from psycopg.types.json import Jsonb

    with _lock:
        with _pg_connect() as conn:
            _pg_ensure_table(conn)
            row = conn.execute("SELECT state FROM scheduler_state WHERE id = 1 FOR UPDATE").fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL has no scheduler state; use the normal state import instead")
            current = row[0]
            local_dataset = state["data"].get("dataset_id")
            current_dataset = (current.get("data") or {}).get("dataset_id")
            if not local_dataset or local_dataset != current_dataset:
                raise RuntimeError("Local and PostgreSQL state use different datasets; refusing to sync")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_state_backups (
                    backup_id BIGSERIAL PRIMARY KEY,
                    state JSONB NOT NULL,
                    source_file TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                "INSERT INTO scheduler_state_backups (state, source_file) VALUES (%s, %s)",
                (Jsonb(current), os.fspath(path)),
            )
            conn.execute(
                "UPDATE scheduler_state SET state = %s, updated_at = NOW() WHERE id = 1",
                (Jsonb(state),),
            )


def audit(st, user, action, detail):
    st["audit"].insert(0, {"at": now(), "user": user, "action": action, "detail": detail})
    del st["audit"][300:]


# ---------------------------------------------------------------- views over the state
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
    out = {"team": TEAM, "dataset_id": st["data"].get("dataset_id"),
           "sessions": [{"session_id": sid, "day": d, "start_period": t, "room_id": r}
                        for sid, (d, t, r) in sorted((st["timetable"] or {}).items())]}
    bookings = []
    for r in st["requests"]:
        if r["status"] == "pending":
            continue
        b = {"request_id": r["id"], "status": "rejected" if r["status"] == "cancelled" else r["status"],
             "reason": r["reason"]}
        if r["status"] in ("approved", "alternative_offered") and r["placement"]:
            b.update(r["placement"])
        bookings.append(b)
    out["fest_week"] = {"bookings": bookings,
                        "session_overrides": [{k: o[k] for k in ("session_id", "day", "start_period", "room_id", "displaced_by")}
                                              for o in st["session_overrides"]]}
    if st["exams"]:
        out["exams"] = st["exams"]
    return out
