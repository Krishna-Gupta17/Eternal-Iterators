"""Transactional JSON state store with one-click reset.

All mutations go through `transaction()`: work on a deep copy, commit (write to disk)
only if the block finishes without raising.
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


def read():
    """A deep copy of the current state (safe to inspect without the lock)."""
    with _lock:
        return copy.deepcopy(_load())


@contextmanager
def transaction():
    global _state
    with _lock:
        work = copy.deepcopy(_load())
        yield work
        _state = work
        _save(work)


def reset(data=None):
    global _state
    with _lock:
        _state = fresh_state(data)
        _save(_state)


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
