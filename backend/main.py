"""FastAPI app: every route that changes data goes through guardrails before it commits."""
import copy
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import ai_service as AI  # noqa: E402
from . import bookings as B  # noqa: E402
from . import dataset_io  # noqa: E402
from . import guardrails as G  # noqa: E402
from . import rbac  # noqa: E402
from . import scheduling as S  # noqa: E402
from . import store  # noqa: E402
from .paths import EXPORTS, ROOT  # noqa: E402
from .rbac import ADMIN, CLUB, COORD, FACULTY  # noqa: E402

app = FastAPI(title="CIT Scheduling & Venue Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
IST = timezone(timedelta(hours=5, minutes=30))
WEEKDAY = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def today_ist():
    return datetime.now(IST).date()


def resolve_date(data, value):
    """A calendar date (YYYY-MM-DD) -> its day code. Past dates and days the institute
    calendar doesn't have (weekends) are refused. The timetable is one repeating week, so
    the date picks which weekday's timetable is checked; the date itself is kept on the record."""
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(400, f"'{value}' is not a date (YYYY-MM-DD)")
    if d < today_ist():
        raise HTTPException(400, f"{d.isoformat()} is in the past. Pick today or a later date.")
    code = WEEKDAY[d.weekday()]
    if code not in data["calendar"]["days"]:
        raise HTTPException(400, f"{d.isoformat()} is a {d.strftime('%A')}; the institute calendar only runs "
                                 f"{data['calendar']['days'][0]}-{data['calendar']['days'][-1]}.")
    return code


def week_of(value):
    d = date.fromisoformat(str(value))
    return (d - timedelta(days=d.weekday())).isoformat()

# ---------------------------------------------------------------- background jobs
_job = {"id": None, "kind": None, "status": "idle", "started": None, "finished": None, "result": None, "error": None}
_job_lock = threading.Lock()


def _run_job(kind, fn):
    with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(409, f"A {_job['kind']} job is already running")
        _job.update(id=store.new_id("JOB"), kind=kind, status="running", started=time.time(),
                    finished=None, result=None, error=None)

    def work():
        try:
            res = fn()
            _job.update(status="done", result=res)
        except S.GuardrailError as e:
            _job.update(status="failed", error=str(e))
        except Exception as e:  # report unexpected failures to the UI instead of hanging the spinner
            _job.update(status="failed", error=f"{type(e).__name__}: {e}")
        _job["finished"] = time.time()

    threading.Thread(target=work, daemon=True).start()
    return dict(_job)


def _no_job():
    if _job["status"] == "running":
        raise HTTPException(409, f"Wait for the running {_job['kind']} job to finish")


def _guard(fn):
    """Run a state mutation; turn guardrail failures into 409s."""
    try:
        with store.transaction() as st:
            return fn(st)
    except S.GuardrailError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/current")
def job_status(user=Depends(rbac.current_user)):
    j = dict(_job)
    if j["started"]:
        j["elapsed"] = round((j["finished"] or time.time()) - j["started"], 1)
    return j


# ---------------------------------------------------------------- auth + meta
class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn):
    return rbac.login(body.username.strip().lower(), body.password)


@app.get("/api/auth/me")
def me(user=Depends(rbac.current_user)):
    return user


@app.get("/api/auth/demo-accounts")
def demo_accounts():
    return [rbac.public_user(u) for u in rbac.USERS]


@app.get("/api/meta")
def meta(user=Depends(rbac.current_user)):
    st = store.view()
    d = st["data"]
    return {"calendar": d["calendar"], "rooms": d["rooms"], "faculty": [{k: f[k] for k in ("id", "name", "department")} for f in d["faculty"]],
            "groups": d["student_groups"], "courses": d["courses"], "priority_order": d["priority_order"],
            "role_category_cap": d["role_category_cap"], "priority_labels": B.LABEL, "llm": AI.status(),
            "dataset_id": d.get("dataset_id"), "institute": d.get("institute"), "today": today_ist().isoformat(),
            "academic_cutoff": G.ACADEMIC_CUTOFF, "academic_last_period": G.academic_last_period(d),
            "room_types": sorted({r["type"] for r in d["rooms"]})}


@app.get("/api/summary")
def summary(user=Depends(rbac.current_user)):
    st = store.view()
    return {"has_timetable": bool(st["timetable"]), "scoreboard": S.scoreboard(st),
            "teaching_hours": len(st["data"]["calendar"]["days"]) * G.academic_last_period(st["data"]),
            "pending_changes": sum(1 for p in _visible_changes(st, user) if p["status"] == "pending"),
            "pending_bookings": sum(1 for r in st["requests"] if r["status"] == "pending" and r.get("proposal")),
            "applied_changes": len(st["changes"]), "sessions": len(st["data"]["sessions"]),
            "requests": len(st["requests"]), "exams": len(st["exams"] or []), "job": dict(_job)}


# ---------------------------------------------------------------- admin
@app.post("/api/admin/reset")
def reset(user=Depends(rbac.require(ADMIN))):
    _no_job()
    store.reset()
    with store.transaction() as st:
        store.audit(st, user["username"], "reset", "Demo data restored to the clean starter dataset")
    return {"ok": True}


@app.post("/api/admin/generate")
def generate(user=Depends(rbac.require(ADMIN))):
    def fn():
        st = store.read()
        rep = S.generate(st, user["username"])
        with store.transaction() as w:
            w.clear()
            w.update(st)
        return {"hard": rep["hard_violation_count"], "soft": rep["soft_total"],
                "booking_score": rep["bookings"]["booking_score"]}
    return _run_job("generate", fn)


@app.get("/api/admin/dataset/format")
def dataset_format(user=Depends(rbac.require(ADMIN))):
    """What the upload zone accepts: the core / optional / overlay CSV files and their columns."""
    cols = {k: list(v) for k, v in dataset_io.SCHEMA.items()}
    return {"core": list(dataset_io.CORE), "core_optional": list(dataset_io.CORE_OPTIONAL),
            "overlays": list(dataset_io.OVERLAYS), "columns": cols,
            "required_columns": {k: [c for c, t in v.items() if not t.startswith("opt_")] for k, v in dataset_io.SCHEMA.items()}}


@app.post("/api/admin/dataset")
async def load_dataset(files: list[UploadFile] = File(...), dry_run: bool = Form(False), clear_missing: bool = Form(False),
                       generate_now: bool = Form(False), user=Depends(rbac.require(ADMIN))):
    """Replace the dataset with a unified cit_public.json or a set of starter-pack CSVs.

    CSVs replace the matching entities of the dataset as it was loaded (before any change
    files). `dry_run` only validates and reports what would change. On success the state is
    rebuilt from scratch (timetable, requests, pending changes cleared), so nothing derived
    from the old entities survives, and `generate_now` starts a fresh timetable generation."""
    _no_job()
    raw = [(f.filename or "upload", await f.read()) for f in files]
    base = store.view()["original_data"]
    try:
        data, info = dataset_io.build(raw, base, clear_missing=clear_missing)
    except dataset_io.DatasetError as e:
        if dry_run:
            return {"ok": False, "errors": e.errors}
        raise HTTPException(400, "Dataset rejected: " + "; ".join(e.errors[:8]))
    info["ok"] = True
    if dry_run:
        return info
    store.reset(data)
    with store.transaction() as st:
        store.audit(st, user["username"], "load-dataset",
                    f"Loaded {data.get('dataset_id')} from {info['mode'].upper()} ({', '.join(info['files'])}): "
                    f"{len(data['sessions'])} sessions, {len(data['rooms'])} rooms; replaced {', '.join(info['replaced'])}")
    if generate_now:
        info["job"] = generate(user)
    return info


class PriorityIn(BaseModel):
    order: list[str]


@app.put("/api/admin/priority")
def set_priority(body: PriorityIn, user=Depends(rbac.require(ADMIN))):
    def fn(st):
        if sorted(body.order) != sorted(st["data"]["priority_order"]):
            raise S.GuardrailError("The new order must contain exactly the same categories")
        st["data"]["priority_order"] = body.order
        store.audit(st, user["username"], "priority", " > ".join(body.order))
        return {"priority_order": body.order}
    return _guard(fn)


@app.get("/api/export/schedule")
def export(user=Depends(rbac.require(ADMIN))):
    st = store.view()
    if not st["timetable"]:
        raise HTTPException(409, "Generate the timetable first")
    EXPORTS.mkdir(exist_ok=True)
    cur = store.export_schedule(st)
    written = []
    if st["history"]:
        (EXPORTS / "schedule.json").write_text(json.dumps(st["generated_schedule"], indent=2), encoding="utf-8")
        (EXPORTS / "schedule_after.json").write_text(json.dumps(cur, indent=2), encoding="utf-8")
        written = ["exports/schedule.json", "exports/schedule_after.json"]
    else:
        (EXPORTS / "schedule.json").write_text(json.dumps(cur, indent=2), encoding="utf-8")
        written = ["exports/schedule.json"]
    return {"schedule": cur, "written": written}


@app.get("/exports/{filename}")
def download_export_file(filename: str):
    if filename in ("schedule.json", "schedule_after.json"):
        f = EXPORTS / filename
        if f.is_file():
            return FileResponse(f, media_type="application/json", filename=filename)
    raise HTTPException(404, "Export file not found")


@app.get("/api/audit")
def audit(user=Depends(rbac.current_user)):
    return store.view()["audit"][:100]


# ---------------------------------------------------------------- timetable views
@app.get("/api/timetable")
def timetable(week7: bool = False, user=Depends(rbac.current_user)):
    st = store.view()
    if not st["timetable"]:
        return {"sessions": [], "bookings": [], "overrides": []}
    d = st["data"]
    courses = {c["id"]: c for c in d["courses"]}
    fac = {f["id"]: f for f in d["faculty"]}
    assign = store.week_assign(st) if week7 else store.assign_of(st)
    out = []
    for s in d["sessions"]:
        if s["id"] not in assign:
            continue
        day, start, room = assign[s["id"]]
        out.append({"id": s["id"], "course_id": s["course_id"], "title": courses.get(s["course_id"], {}).get("title", s["course_id"]),
                    "kind": s["kind"], "groups": s["groups"], "faculty_id": s["faculty_id"],
                    "faculty_name": fac.get(s["faculty_id"], {}).get("name", s["faculty_id"]),
                    "department": courses.get(s["course_id"], {}).get("department"),
                    "day": day, "start_period": start, "duration": s["duration"], "room_id": room,
                    "size": s["size"], "basket_slot": s.get("basket_slot")})
    bks = [dict(b, title=next(r["request"]["title"] for r in st["requests"] if r["id"] == b["request_id"]))
           for b in store.placed_bookings(st)]
    return {"sessions": out, "bookings": bks, "overrides": st["session_overrides"]}


@app.get("/api/exams")
def exams(user=Depends(rbac.current_user)):
    st = store.view()
    ex = {e["id"]: e for e in st["data"].get("exams", [])}
    courses = {c["id"]: c["title"] for c in st["data"]["courses"]}
    return {"calendar": st["data"]["calendar"]["exam_week"],
            "exams": [dict(e, groups=ex[e["exam_id"]]["groups"], size=ex[e["exam_id"]]["size"],
                           title=", ".join(courses.get(c, c) for c in ex[e["exam_id"]]["course_ids"]),
                           seats=sum(r["capacity"] // 2 for r in st["data"]["rooms"] if r["id"] in e["rooms"]))
                      for e in (st["exams"] or [])]}


@app.get("/api/rooms/utilisation")
def utilisation(user=Depends(rbac.current_user)):
    st = store.view()
    d = st["data"]
    ctx = G.V.Ctx(d)
    teach = len(ctx.days) * G.academic_last_period(d)
    used, fill = {}, {}
    for sid, (day, start, rid) in store.assign_of(st).items():
        s = ctx.sessions.get(sid)
        if not s:
            continue
        used[rid] = used.get(rid, 0) + s["duration"]
        fill.setdefault(rid, []).append(s["size"] / ctx.rooms[rid]["capacity"])
    out = []
    for r in d["rooms"]:
        closed = sum(1 for (day, p) in ctx.room_un[r["id"]] if p <= G.academic_last_period(d))
        out.append({"room_id": r["id"], "name": r["name"], "type": r["type"], "capacity": r["capacity"],
                    "accessible": r["accessible"], "hours_used": used.get(r["id"], 0), "hours_closed": closed,
                    "utilisation": round(used.get(r["id"], 0) / teach, 3),
                    "avg_fill": round(sum(fill[r["id"]]) / len(fill[r["id"]]), 3) if r["id"] in fill else None,
                    "teaching_room": any(u in r["allowed_uses"] for u in ("lecture", "tutorial", "lab"))})
    return out


@app.get("/api/rooms/free")
def free_rooms(day: str, start: int, duration: int = 1, min_capacity: int = 0, room_type: str = "",
               accessible: bool = False, week7: bool = False, user=Depends(rbac.current_user)):
    st = store.view()
    assign = store.week_assign(st) if week7 else store.assign_of(st)
    occ = G.Occupancy(st["data"], assign, store.placed_bookings(st) if week7 else [])
    per = list(range(start, start + duration))
    if per[-1] > G.MAX_PERIOD:
        raise HTTPException(400, "That runs past P13 (22:00)")
    return [{"room_id": r["id"], "name": r["name"], "type": r["type"], "capacity": r["capacity"],
             "features": r["features"], "accessible": r["accessible"], "allowed_uses": r["allowed_uses"]}
            for r in occ.free_rooms(day, per, min_capacity, room_type or None, accessible)]


# ---------------------------------------------------------------- bookings
class WindowIn(BaseModel):
    day: str = ""
    date: str | None = None        # YYYY-MM-DD; when given it decides the day
    from_period: int = Field(ge=1, le=16)
    to_period: int = Field(ge=1, le=16)


class PrefIn(BaseModel):
    windows: list[WindowIn]
    requested_room: str | None = None


class BookingIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    attendance: int = Field(ge=1, le=5000)
    duration_periods: int = Field(ge=1, le=13)
    windows: list[WindowIn] = Field(min_length=1)
    room_types: list[str] = Field(min_length=1)
    needs: list[str] = []
    requested_room: str | None = None
    mandatory_for_groups: list[str] = []
    requested_category: str | None = None
    fallbacks: list[PrefIn] = []
    series: bool = False
    raw_text: str = ""


def _windows(st, wins):
    """Form windows with dates resolved to day codes (past dates and weekends refused)."""
    out = []
    for w in wins:
        w = w.model_dump() if isinstance(w, BaseModel) else dict(w)
        if w.get("date"):
            w["day"] = resolve_date(st["data"], w["date"])
            w["week_of"] = week_of(w["date"])
        else:
            w.pop("date", None)
        if w["day"] not in st["data"]["calendar"]["days"]:
            raise HTTPException(400, f"Pick a date for every time window ({w['day'] or 'no day'} is not a teaching day)")
        out.append(w)
    return out


def _req_from_form(st, form: BookingIn, user, window=None):
    role = user["role"]
    cap = st["data"]["role_category_cap"].get(role, "club_event")
    cat = form.requested_category if form.requested_category in st["data"]["priority_order"] else cap
    windows = [window] if window else _windows(st, form.windows)
    fallbacks = [] if window else [dict(f.model_dump(), windows=_windows(st, f.windows)) for f in form.fallbacks]
    return {"id": store.new_id("REQ"), "submitted_at": datetime.now(IST).isoformat(timespec="seconds"),
            "requester": user.get("club") or user["name"], "role": role, "department": user.get("department"),
            "requested_category": cat, "title": form.title, "attendance": form.attendance,
            "duration_periods": form.duration_periods, "windows": windows, "room_types": form.room_types,
            "needs": form.needs, "requested_room": form.requested_room or None,
            "mandatory_for_groups": form.mandatory_for_groups, "series_id": None, "raw_text": form.raw_text,
            "fallbacks": fallbacks}


def _require_timetable(st):
    if not st["timetable"]:
        raise S.GuardrailError("The Admin has to generate the timetable before bookings can be checked.")


@app.get("/api/bookings")
def list_bookings(user=Depends(rbac.current_user)):
    st = store.view()
    out = []
    for r in st["requests"]:
        q = r["request"]
        cat = G.effective_category(st["data"], q)
        rule = B.approver_rule(st, r) if r["status"] == "pending" and r.get("proposal") else None
        out.append(dict(r, effective_category=cat, priority_rank=B.rank(st["data"], cat),
                        approver=rule, mine=r["submitted_by"] == user["username"],
                        can_approve=bool(rule) and rbac.can_approve_booking(user, rule) and r["submitted_by"] != user["username"]))
    return out


@app.post("/api/bookings/parse")
def parse(text: str = Body(..., embed=True), today: str = Body("MON", embed=True), user=Depends(rbac.current_user)):
    if not text.strip():
        raise HTTPException(400, "Type a request first")
    st = store.view()
    return AI.parse_booking(text[:2000], st["data"], today.upper()[:3], user)


@app.post("/api/bookings/preview")
def preview(form: BookingIn, user=Depends(rbac.current_user)):
    st = store.view()
    if not st["timetable"]:
        return [{"status": "unknown", "reason": "Generate the timetable first."}]
    windows = _windows(st, form.windows)
    out = []
    for w in (windows if form.series else [None]):
        req = _req_from_form(st, form, user, w)
        prop = B.evaluate(st, req)
        prop["effective_category"] = G.effective_category(st["data"], req)
        _gate(st, prop, user)
        out.append(prop)
    return out


def _gate(st, prop, user):
    """Availability verdict for the Check -> Confirm & submit workflow. A slot that only an
    approved class, exam or confirmed booking could free is unavailable and can't be submitted;
    other people's queued (pending) requests don't block it until an approver picks one."""
    prop["available"] = prop["status"] in ("approved", "needs_approval")
    prop["pending_queue"] = B.pending_competitors(st, prop["placement"]) if prop["available"] else []
    prop["queued"] = prop["available"] and B.needs_human(st, {}, prop, queue=user["role"] != ADMIN)
    if not prop["available"]:
        prop["unavailable_reason"] = prop["reason"]
    return prop


class CheckIn(BaseModel):
    form: BookingIn
    room_id: str
    day: str
    start_period: int


@app.post("/api/bookings/check")
def check_slot(body: CheckIn, user=Depends(rbac.current_user)):
    """Live hard-constraint feedback for the manual event scheduling form."""
    st = store.view()
    if not st["timetable"]:
        return {"ok": False, "violations": ["Generate the timetable first."]}
    req = _req_from_form(st, body.form, user)
    occ = G.Occupancy(st["data"], store.week_assign(st), store.placed_bookings(st))
    end = body.start_period + body.form.duration_periods - 1
    v = occ.booking_violations(req, body.room_id, body.day, body.start_period, end)
    inside = any(w["day"] == body.day and w["from_period"] <= body.start_period and end <= w["to_period"] for w in req["windows"])
    return {"ok": not v, "violations": v, "inside_window": inside}


@app.post("/api/bookings")
def create_booking(form: BookingIn, user=Depends(rbac.current_user)):
    _no_job()

    def fn(st):
        _require_timetable(st)
        made = []
        windows = _windows(st, form.windows)
        props = []
        for w in (windows if form.series else [None]):
            req = _req_from_form(st, form, user, w)
            prop = _gate(st, B.evaluate(st, req), user)
            if not prop["available"]:
                when = f" ({w['date'] or w['day']})" if w else ""
                raise S.GuardrailError(f"Unavailable{when}: {prop['reason']} Check availability again and pick a free slot.")
            props.append((req, prop))
        for req, prop in props:
            rec = {"id": req["id"], "request": req, "submitted_by": user["username"], "status": "pending",
                   "placement": None, "reason": "", "decided_by": None, "displaces": [], "overrides": [], "accepted": None}
            rec["proposal"] = prop
            if B.needs_human(st, rec, prop, queue=user["role"] != ADMIN):
                rec.update(placement=prop["placement"], displaces=prop.get("displaces", []),
                           overrides=prop.get("overrides", []))
                rule = B.approver_rule(st, rec)
                who = "an Admin" if rule == "admin" else f"the {rule.split(':')[1]} coordinator or an Admin"
                queue = B.pending_competitors(st, prop["placement"])
                rec["reason"] = (prop["reason"].replace(" Needs Admin approval.", "") + f" Waiting for approval by {who}."
                                 + (f" {len(queue)} other request(s) for this room and time are also in the queue "
                                    f"({', '.join(queue)}); the first one approved gets it." if queue else ""))
            else:
                rec.update(status=prop["status"], placement=prop["placement"], reason=prop["reason"],
                           decided_by="allocation engine")
            st["requests"].append(rec)
            made.append(rec)
            store.audit(st, user["username"], "booking", f"{rec['id']} {req['title']}: {rec['status']}")
        return made
    return _guard(fn)


def _find_req(st, rid):
    rec = next((r for r in st["requests"] if r["id"] == rid), None)
    if rec is None:
        raise HTTPException(404, "No such request")
    return rec


@app.post("/api/bookings/{rid}/approve")
def approve_booking(rid: str, user=Depends(rbac.require(ADMIN, COORD))):
    _no_job()

    def fn(st):
        rec = _find_req(st, rid)
        rbac.forbid_self_approval(user, rec["submitted_by"])
        if rec["status"] != "pending" or not rec.get("proposal"):
            raise S.GuardrailError("This request is not waiting for approval")
        rule = B.approver_rule(st, rec)
        if not rbac.can_approve_booking(user, rule):
            raise HTTPException(403, f"Only {'an Admin' if rule == 'admin' else 'the ' + rule.split(':')[1] + ' coordinator or an Admin'} can approve this")
        before = copy.deepcopy(st)
        for h in rec["displaces"]:
            other = _find_req(st, h)
            other["status"], other["placement"] = "rejected", None
        st["session_overrides"] += rec["overrides"]
        pl = rec["placement"]
        occ = G.Occupancy(st["data"], store.week_assign(st), store.placed_bookings(st, ignore=[rid]), ignore=[rid])
        v = occ.booking_violations(rec["request"], pl["room_id"], pl["day"], pl["start_period"], pl["end_period"])
        if v:
            raise S.GuardrailError("The slot is no longer free: " + "; ".join(v[:3]))
        inside = any(w["day"] == pl["day"] and w["from_period"] <= pl["start_period"] and pl["end_period"] <= w["to_period"]
                     for w in rec["request"]["windows"])
        rec["status"] = "approved" if inside else "alternative_offered"
        rec["decided_by"] = user["username"]
        rec["reason"] = B.approved_reason(st["data"], rec["request"], pl["room_id"], pl["day"], pl["start_period"]) + \
            f" Approved by {user['name']}."
        for h in rec["displaces"]:
            other = _find_req(st, h)
            prop = B.evaluate(st, other["request"])
            if prop["status"] in ("approved", "alternative_offered"):
                other["status"], other["placement"] = prop["status"], prop["placement"]
            other["reason"] = (f"Displaced by {rid} '{rec['request']['title']}' "
                               f"({B.priority_text(st['data'], G.effective_category(st['data'], rec['request']))}). " + prop["reason"])
        S.commit_guard(before, st)
        lost = B.settle_competitors(st, rec, user["name"])
        store.audit(st, user["username"], "approve-booking", rid + (f" (rejected competing {', '.join(lost)})" if lost else ""))
        return dict(rec, rejected_competitors=lost)
    return _guard(fn)


@app.post("/api/bookings/{rid}/reject")
def reject_booking(rid: str, reason: str = Body("", embed=True), user=Depends(rbac.require(ADMIN, COORD))):
    def fn(st):
        rec = _find_req(st, rid)
        rbac.forbid_self_approval(user, rec["submitted_by"])
        if rec["status"] != "pending":
            raise S.GuardrailError("Only pending requests can be rejected")
        rule = B.approver_rule(st, rec)
        if not rbac.can_approve_booking(user, rule):
            raise HTTPException(403, "You cannot decide this request")
        rec.update(status="rejected", placement=None, decided_by=user["username"],
                   reason=f"Rejected by {user['name']}: {reason.strip() or 'no reason given'}")
        store.audit(st, user["username"], "reject-booking", rid)
        return rec
    return _guard(fn)


@app.post("/api/bookings/{rid}/cancel")
def cancel_booking(rid: str, user=Depends(rbac.current_user)):
    def fn(st):
        rec = _find_req(st, rid)
        if rec["submitted_by"] != user["username"] and user["role"] != ADMIN:
            raise HTTPException(403, "Only the requester or an Admin can cancel a request")
        if rec["status"] == "cancelled":
            raise S.GuardrailError("Already cancelled")
        rec.update(status="cancelled", placement=None, reason=f"Cancelled by {user['name']} on {store.now()[:10]}.")
        store.audit(st, user["username"], "cancel-booking", rid)
        return rec
    return _guard(fn)


@app.post("/api/bookings/{rid}/alternative")
def answer_alternative(rid: str, accept: bool = Body(..., embed=True), user=Depends(rbac.current_user)):
    def fn(st):
        rec = _find_req(st, rid)
        if rec["submitted_by"] != user["username"] and user["role"] != ADMIN:
            raise HTTPException(403, "Only the requester can answer an alternative")
        if rec["status"] != "alternative_offered":
            raise S.GuardrailError("No alternative is on offer")
        if accept:
            rec["accepted"] = True
            rec["reason"] += " Requester accepted the alternative."
        else:
            rec.update(status="rejected", placement=None, accepted=False,
                       reason=rec["reason"] + " Requester declined the alternative.")
        return rec
    return _guard(fn)


# ---------------------------------------------------------------- staged timetable changes
def _visible_changes(st, user):
    """Staged changes this user may see: Admins see all, faculty and coordinators their own.
    The pending counter and the list both use this, so they always agree."""
    if user["role"] in (FACULTY, COORD):
        return [p for p in st["pending"] if p.get("requested_by") == user["username"]]
    return st["pending"]


@app.get("/api/changes")
def list_changes(user=Depends(rbac.current_user)):
    st = store.view()
    return [dict(p, proposed_timetable=None,
                 can_approve=user["role"] == ADMIN and p["status"] == "pending" and p.get("requested_by") != user["username"])
            for p in _visible_changes(st, user)]


def _propose_job(changes, user, source, requested_by=None):
    def fn():
        st = store.read()
        item = S.propose(st, changes, user["username"], source, requested_by=requested_by)
        with store.transaction() as w:
            w["pending"].insert(0, item)
            store.audit(w, user["username"], "propose-change", f"{item['id']} ({source}): {item['status']}")
        return {"id": item["id"], "status": item["status"], "moved": item.get("moved_count")}
    return _run_job("reschedule", fn)


@app.post("/api/changes/upload")
async def upload_changes(file: UploadFile, user=Depends(rbac.require(ADMIN))):
    try:
        dis = json.loads(await file.read())
        changes = dis["changes"]
        assert isinstance(changes, list) and changes
    except (ValueError, KeyError, AssertionError):
        raise HTTPException(400, "Not a change file: expected {\"changes\": [...]} in the starter-pack format")
    return _propose_job(changes, user, f"change file {file.filename}")


@app.post("/api/changes/simulate")
def simulate(change: dict = Body(...), user=Depends(rbac.require(ADMIN))):
    return _propose_job([change], user, "admin simulation")


@app.post("/api/changes/{cid}/approve")
def approve_change(cid: str, user=Depends(rbac.require(ADMIN))):
    _no_job()

    def fn(st):
        item = next((p for p in st["pending"] if p["id"] == cid), None)
        if item is None:
            raise HTTPException(404, "No such change")
        rbac.forbid_self_approval(user, item.get("requested_by"))
        return dict(S.approve(st, cid, user["username"]), proposed_timetable=None)
    return _guard(fn)


@app.post("/api/changes/{cid}/reject")
def reject_change(cid: str, note: str = Body("", embed=True), user=Depends(rbac.require(ADMIN))):
    def fn(st):
        item = next((p for p in st["pending"] if p["id"] == cid), None)
        if item is None:
            raise HTTPException(404, "No such change")
        rbac.forbid_self_approval(user, item.get("requested_by"))
        return dict(S.reject(st, cid, user["username"], note), proposed_timetable=None)
    return _guard(fn)


@app.post("/api/changes/{cid}/recompute")
def recompute_change(cid: str, user=Depends(rbac.require(ADMIN))):
    """Re-solve a proposal that went out of date because another change was approved first."""
    _no_job()
    item = next((p for p in store.view()["pending"] if p["id"] == cid), None)
    if item is None:
        raise HTTPException(404, "No such change")
    if item["status"] != "outdated":
        raise HTTPException(409, "Only outdated proposals need recomputing")
    with store.transaction() as st:
        old = next(p for p in st["pending"] if p["id"] == cid)
        old.update(status="rejected", decided_by=user["username"], decided_at=store.now(), note="Replaced by a recomputed proposal.")
    return _propose_job(item["changes"], user, item["source"] + " (recomputed)", requested_by=item.get("requested_by"))


# ---------------------------------------------------------------- faculty / coordinator requests
def _my_sessions(st, user):
    d = st["data"]
    courses = {c["id"]: c for c in d["courses"]}
    assign = store.assign_of(st)
    if user["role"] == FACULTY:
        ss = [s for s in d["sessions"] if s["faculty_id"] == user["faculty_id"]]
    elif user["role"] == COORD:
        ss = [s for s in d["sessions"] if courses.get(s["course_id"], {}).get("department") == user["department"]]
    else:
        ss = d["sessions"]
    out = []
    for s in ss:
        if s["id"] in assign:
            dd, t, r = assign[s["id"]]
            out.append(dict(s, slot=f"{dd} P{t}", day=dd, start_period=t, room_id=r))
    return out


def _owned_session(st, user, sid):
    s = next((x for x in _my_sessions(st, user) if x["id"] == sid), None)
    if s is None:
        raise HTTPException(403, "You can only change your own (or your department's) sessions")
    return s


class AssistantIn(BaseModel):
    text: str
    today: str = "MON"


@app.post("/api/assistant/faculty")
def faculty_assistant(body: AssistantIn, user=Depends(rbac.require(FACULTY, COORD, ADMIN))):
    st = store.view()
    if not st["timetable"]:
        raise HTTPException(409, "The timetable has not been generated yet")
    mine = _my_sessions(st, user)
    intent, source, err = AI.parse_faculty(body.text[:1000], body.today.upper()[:3], mine)
    res = {"intent": intent, "source": source, "llm_error": err, "candidates": [], "options": [], "blocked": [],
           "message": ""}
    if intent.get("security_flags"):
        res["security"] = "Instructions to skip checks or act as someone else were ignored; every option below is validated by code."
    kind = intent["intent"]
    if kind == "MARK_UNAVAILABILITY":
        days = intent.get("days") or []
        periods = intent.get("periods") or list(range(1, 10))
        if not days:
            res["message"] = "Which day(s) will you be unavailable?"
            return res
        fid = user.get("faculty_id")
        hit = [s for s in mine if s["day"] in days and set(G.V.block(s["start_period"], s["duration"])) & set(periods)
               and (not fid or s["faculty_id"] == fid)]
        res["unavailability"] = {"days": days, "periods": periods, "reason": intent.get("reason", "")}
        res["affected"] = [{"id": s["id"], "course_id": s["course_id"], "slot": s["slot"], "room_id": s["room_id"]} for s in hit]
        res["message"] = (f"{len(hit)} of your sessions fall in that time. Submitting creates a rescheduling proposal "
                          f"(fewest moves) for Admin approval." if hit else
                          "None of your sessions fall in that time; submitting just records the unavailability.")
        return res
    if kind == "ADD_EXTRA_SESSION":
        cid = intent.get("course_id")
        base = next((s for s in mine if s["course_id"] == cid), None)
        if base is None:
            res["message"] = "Which of your courses is the extra session for? " + \
                ", ".join(sorted({s["course_id"] for s in mine}))
            return res
        session = _extra_session(st, base, intent.get("duration") or 1)
        opts, blocked = S.move_options(st, session, intent.get("target_days") or intent.get("days") or None,
                                       intent.get("target_periods") or intent.get("periods"))
        res.update(session=session, options=opts, blocked=blocked,
                   message=(f"{len(opts)} clash-free slot(s) found for an extra {session['kind']} of {cid}."
                            if opts else "No clash-free slot fits that request."))
        return res
    if kind == "MOVE_SESSION":
        cands = mine
        if intent.get("session_id"):
            cands = [s for s in mine if s["id"] == intent["session_id"]]
        else:
            if intent.get("course_id"):
                cands = [s for s in cands if s["course_id"] == intent["course_id"]]
            if intent.get("days"):
                cands = [s for s in cands if s["day"] in intent["days"]]
            if intent.get("periods"):
                cands = [s for s in cands if set(G.V.block(s["start_period"], s["duration"])) & set(intent["periods"])]
        res["candidates"] = [{"id": s["id"], "course_id": s["course_id"], "kind": s["kind"], "slot": s["slot"],
                              "room_id": s["room_id"]} for s in cands[:12]]
        if len(cands) != 1:
            res["message"] = ("Which session do you mean? Pick one below." if cands else
                              "None of your sessions match that description.")
            return res
        s = cands[0]
        opts, blocked = S.move_options(st, s, intent.get("target_days"), intent.get("target_periods"))
        res.update(session=s, options=opts, blocked=blocked,
                   message=(f"{len(opts)} clash-free option(s) for {s['id']} (now {s['slot']} in {s['room_id']})."
                            if opts else f"{s['id']} cannot move there without breaking a hard constraint:"))
        return res
    res["message"] = "I can move one of your sessions, add an extra session, or record unavailability. What do you need?"
    return res


def _extra_session(st, base, duration, kind=None, groups=None):
    n = sum(1 for s in st["data"]["sessions"] if s["course_id"] == base["course_id"] and "-X" in s["id"]) + 1
    kind = kind or ("tutorial" if duration == 1 else "lecture")
    # an extra lab keeps its lab's room type; any other class uses the course's theory rooms
    if kind == "lab" or base["kind"] != "lab":
        room_types = base["room_types"]
    else:
        room_types = ["classroom", "lecture_hall", "auditorium"]
    size = base["size"]
    if groups and set(groups) != set(base["groups"]):
        sizes = {g["id"]: g["size"] for g in st["data"]["student_groups"]}
        size = sum(sizes.get(g, 0) for g in groups)
    return {"id": f"{base['course_id']}-X{n}", "course_id": base["course_id"], "kind": kind, "groups": groups or base["groups"],
            "faculty_id": base["faculty_id"], "size": size, "duration": duration,
            "room_types": room_types, "needs_accessible": base.get("needs_accessible", False), "basket_slot": None, "fixed_slot": None}


@app.get("/api/requests/my-sessions")
def my_sessions(user=Depends(rbac.require(FACULTY, COORD, ADMIN))):
    """Sessions this user may reschedule or add an extra class to, with their room requirements."""
    st = store.view()
    courses = {c["id"]: c["title"] for c in st["data"]["courses"]}
    return [dict(s, title=courses.get(s["course_id"], s["course_id"])) for s in _my_sessions(st, user)]


class SessionMoveIn(BaseModel):
    session_id: str = ""
    day: str = ""
    date: str | None = None      # YYYY-MM-DD target date; decides the day when given
    start_period: int
    room_id: str
    room_type: str | None = None  # required room type picked on the form (strictly matched)
    note: str = ""
    extra: dict | None = None    # ADD_EXTRA_SESSION: {course_id, duration, kind?, groups?, faculty_id?}


def _academic_plan(st, user, body: SessionMoveIn):
    """Resolve a reschedule / extra-class request to (session, changes, data_after, day, room).
    A room type that contradicts the session or the room is a malformed request (400)."""
    data = st["data"]
    day = resolve_date(data, body.date) if body.date else body.day
    if day not in data["calendar"]["days"]:
        raise HTTPException(400, "Pick a target date")
    if body.extra:
        ex = body.extra
        mine = [s for s in _my_sessions(st, user) if s["course_id"] == ex.get("course_id")
                and (not ex.get("faculty_id") or s["faculty_id"] == ex["faculty_id"])]
        groups = ex.get("groups") or None
        kind = ex.get("kind")
        cands = [s for s in mine if not groups or set(groups) <= set(s["groups"])]
        base = (next((s for s in cands if s["kind"] == kind), None) if kind else None) or (cands[0] if cands else None)
        if base is None:
            raise HTTPException(403, "You can only add sessions to your own courses, for groups that take them")
        if kind == "lab" and base["kind"] != "lab":
            raise HTTPException(400, f"{base['course_id']} has no lab sessions for these groups")
        duration = base["duration"] if kind == "lab" else int(ex.get("duration") or 1)
        session = _extra_session(st, base, duration, kind=kind, groups=groups)
        if body.room_type:
            if body.room_type not in session["room_types"]:
                raise HTTPException(400, f"H6: an extra {session['kind']} of {base['course_id']} needs a "
                                         f"{' / '.join(session['room_types'])} room, not {body.room_type}")
            session["room_types"] = [body.room_type]       # the form pins one room type: strict match
        changes = [{"type": "add_session", "session": session}]
        data_after = G.V.apply_disruption(data, {"changes": changes})
    else:
        session = _owned_session(st, user, body.session_id)
        if body.room_type and body.room_type not in session["room_types"]:
            raise HTTPException(400, f"H6: {session['id']} needs a {' / '.join(session['room_types'])} room, not {body.room_type}")
        changes, data_after = [], data
    room = next((r for r in data["rooms"] if r["id"] == body.room_id), None)
    if room is None:
        raise HTTPException(400, f"Unknown room {body.room_id}")
    if body.room_type and room["type"] != body.room_type:
        raise HTTPException(400, f"H6: {room['id']} is a {room['type']}, the request needs a {body.room_type}")
    return session, changes, data_after, day, room


def _pending_session_conflicts(st, session, day, start, room_id):
    """Queued (not yet approved) hand-picked moves that want the same room, faculty or groups then."""
    per = set(G.V.block(start, session["duration"]))
    base = {s["id"]: s for s in st["data"]["sessions"]}
    out = []
    for p in st["pending"]:
        if p["status"] != "pending" or not p.get("explicit"):
            continue
        sess = dict(base, **{c["session"]["id"]: c["session"] for c in p["changes"] if c["type"] == "add_session"})
        for sid, (d, t, r) in p["explicit"].items():
            o = sess.get(sid)
            if sid == session["id"] or o is None or d != day or not per & set(G.V.block(t, o["duration"])):
                continue
            if r == room_id or o["faculty_id"] == session["faculty_id"] or set(o["groups"]) & set(session["groups"]):
                out.append(p["id"])
                break
    return out


def _academic_check(st, user, body):
    """-> (session, changes, day, violations, invalid). `invalid` means the request itself breaks
    the room-type or lab-window rules, so no clash check is run and it can never be submitted."""
    session, changes, data_after, day, room = _academic_plan(st, user, body)
    bad = G.class_request_problems(data_after, session, body.start_period, room)
    if bad:
        return session, changes, day, bad, True
    occ = G.Occupancy(data_after, store.assign_of(st), store.placed_bookings(st), ignore=[session["id"]])
    return session, changes, day, occ.session_violations(session, day, body.start_period, body.room_id), False


@app.post("/api/requests/check")
def check_academic(body: SessionMoveIn, user=Depends(rbac.require(FACULTY, COORD, ADMIN))):
    """Check availability for a reschedule / extra class before it can be submitted."""
    st = store.view()
    if not st["timetable"]:
        return {"available": False, "violations": ["Generate the timetable first."], "pending_queue": []}
    session, _, day, v, invalid = _academic_check(st, user, body)
    queue = [] if v else _pending_session_conflicts(st, session, day, body.start_period, body.room_id)
    return {"available": not v, "violations": v, "invalid": invalid, "day": day, "date": body.date, "pending_queue": queue,
            "session": {k: session[k] for k in ("id", "course_id", "kind", "groups", "faculty_id", "size", "duration", "room_types")}}


@app.post("/api/requests/session-move")
def request_move(body: SessionMoveIn, user=Depends(rbac.require(FACULTY, COORD, ADMIN))):
    _no_job()

    def fn(st):
        _require_timetable(st)
        session, changes, day, v, invalid = _academic_check(st, user, body)
        if invalid:
            raise HTTPException(400, "Request blocked: " + "; ".join(v))
        if v:
            raise S.GuardrailError("That slot breaks hard constraints: " + "; ".join(v[:4]))
        queue = _pending_session_conflicts(st, session, day, body.start_period, body.room_id)
        what = "extra session" if body.extra else "move"
        when = f" on {body.date} ({day})" if body.date else ""
        item = S.propose(st, changes, user["username"], f"{what} request by {user['name']}{when}"
                         + (f": {body.note}" if body.note else ""), requested_by=user["username"],
                         explicit={session["id"]: (day, body.start_period, body.room_id)})
        item["effective_date"] = body.date
        # what was asked for, so request lists can show it without unpacking the diff
        item["request"] = {"kind": what, "session_id": session["id"], "course_id": session["course_id"],
                           "session_kind": session["kind"], "faculty_id": session["faculty_id"], "groups": session["groups"],
                           "date": body.date, "day": day, "start_period": body.start_period,
                           "end_period": body.start_period + session["duration"] - 1, "room_id": body.room_id}
        if queue:
            item["queue_note"] = (f"{len(queue)} other pending request(s) want this slot ({', '.join(queue)}); "
                                  f"approving one rejects the others.")
        store.audit(st, user["username"], "request-move", f"{item['id']} {session['id']} -> {day} P{body.start_period} {body.room_id}{when}")
        return dict(item, proposed_timetable=None)
    return _guard(fn)


class UnavailIn(BaseModel):
    days: list[str] = []
    dates: list[str] = []        # YYYY-MM-DD; when given they decide the days
    periods: list[int] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)
    faculty_id: str | None = None


def _leave_change(st, user, body: UnavailIn):
    fid = user.get("faculty_id") if user["role"] == FACULTY else body.faculty_id
    if not fid:
        raise HTTPException(400, "Which faculty member?")
    if not any(f["id"] == fid for f in st["data"]["faculty"]):
        raise HTTPException(400, f"Unknown faculty {fid}")
    if not body.reason.strip():
        raise HTTPException(400, "Give a reason for the leave")
    cal_days = st["data"]["calendar"]["days"]
    dates = sorted(set(body.dates))
    if dates:
        days = sorted({resolve_date(st["data"], d) for d in dates}, key=cal_days.index)
    else:
        days = [d for d in body.days if d in cal_days]
    periods = sorted({p for p in body.periods if 1 <= p <= G.LAST_TEACHING})
    if not days or not periods:
        raise HTTPException(400, "Pick the leave date(s) and time slots")
    ch = {"type": "faculty_unavailable", "faculty_id": fid, "days": days, "periods": periods, "reason": body.reason.strip()}
    if dates:
        ch["dates"] = dates
    return ch


@app.post("/api/requests/unavailability/check")
def check_unavailability(body: UnavailIn, user=Depends(rbac.require(FACULTY, ADMIN))):
    """Which of the faculty member's classes a leave would hit (the check step before submitting)."""
    st = store.view()
    ch = _leave_change(st, user, body)
    assign = store.assign_of(st)
    hit = []
    for s in st["data"]["sessions"]:
        if s["faculty_id"] != ch["faculty_id"] or s["id"] not in assign:
            continue
        d, t, r = assign[s["id"]]
        if d in ch["days"] and set(G.V.block(t, s["duration"])) & set(ch["periods"]):
            hit.append({"id": s["id"], "course_id": s["course_id"], "slot": f"{d} P{t}", "room_id": r})
    open_leave = [p["id"] for p in st["pending"] if p["status"] == "pending"
                  and any(c["type"] == "faculty_unavailable" and c["faculty_id"] == ch["faculty_id"] for c in p["changes"])]
    return {"available": True, "change": ch, "affected": hit, "pending_queue": open_leave}


@app.post("/api/requests/unavailability")
def request_unavailability(body: UnavailIn, user=Depends(rbac.require(FACULTY, ADMIN))):
    ch = _leave_change(store.view(), user, body)
    when = ", ".join(ch.get("dates") or ch["days"])
    return _propose_job([ch], user, f"leave request by {user['name']} ({when})",
                        requested_by=user["username"] if user["role"] == FACULTY else None)


# ---------------------------------------------------------------- explanations
@app.get("/api/explain/session/{sid}")
def explain_session(sid: str, user=Depends(rbac.current_user)):
    st = store.view()
    if sid not in (st["timetable"] or {}):
        raise HTTPException(404, "That session is not scheduled")
    facts = S.explain_session(st, sid)
    text, src = AI.phrase(f"Why is {sid} at {facts['slot']} in {facts['room']}?", facts, S.explain_text(facts))
    return {"facts": facts, "text": text, "source": src}


@app.get("/api/explain/booking/{rid}")
def explain_booking(rid: str, user=Depends(rbac.current_user)):
    st = store.view()
    rec = _find_req(st, rid)
    q = rec["request"]
    cat = G.effective_category(st["data"], q)
    facts = {"request": rid, "title": q["title"], "requester": q["requester"], "status": rec["status"],
             "category_requested": q["requested_category"], "category_effective": cat,
             "priority": B.priority_text(st["data"], cat), "decision_reason": rec["reason"],
             "placement": rec["placement"], "windows": q["windows"]}
    if q["requested_category"] != cat:
        facts["category_note"] = (f"{q['role']} requests are capped at {B.LABEL[st['data']['role_category_cap'][q['role']]]}; "
                                  f"the claimed {B.LABEL.get(q['requested_category'], q['requested_category'])} was not accepted.")
    fallback = f"{rid} '{q['title']}' ({facts['priority']}) is {rec['status'].replace('_', ' ')}. {rec['reason']}" + \
        (f" {facts['category_note']}" if "category_note" in facts else "")
    text, src = AI.phrase(f"Why was booking {rid} {rec['status']}?", facts, fallback)
    return {"facts": facts, "text": text, "source": src}


@app.get("/api/explain/exam/{eid}")
def explain_exam(eid: str, user=Depends(rbac.current_user)):
    st = store.view()
    e = next((x for x in st["exams"] or [] if x["exam_id"] == eid), None)
    if e is None:
        raise HTTPException(404, "No such exam")
    ex = next(x for x in st["data"]["exams"] if x["id"] == eid)
    rooms = {r["id"]: r for r in st["data"]["rooms"]}
    seats = sum(rooms[r]["capacity"] // 2 for r in e["rooms"])
    same_day = [x["exam_id"] for x in st["exams"] if x["day"] == e["day"] and x["exam_id"] != eid
                and set(next(y for y in st["data"]["exams"] if y["id"] == x["exam_id"])["groups"]) & set(ex["groups"])]
    text = (f"{eid} ({ex['size']} students: {', '.join(ex['groups'])}) is on {e['day']} {e['session']} in {', '.join(e['rooms'])}. "
            f"Rooms seat half their capacity during exams, giving {seats} seats. "
            + (f"These groups have no other exam that day." if not same_day else f"Same-day exams: {', '.join(same_day)}.")
            + " The exam solver gives every group at most one exam per day where possible, then uses the fewest rooms.")
    return {"facts": {"exam": e, "size": ex["size"], "seats": seats}, "text": text, "source": "template"}


@app.post("/api/ask")
def ask(question: str = Body(..., embed=True), user=Depends(rbac.current_user)):
    st = store.view()
    q = question.strip()
    ids = {r["id"].upper(): r["id"] for r in st["requests"]}
    m = re.search(r"\b(BR-\d+[a-z]?|REQ-[0-9A-F]{6})\b", q, re.I)
    if m and m.group(1).upper() in ids:
        return explain_booking(ids[m.group(1).upper()], user)
    m = re.search(r"\b(EX-[A-Z0-9-]+)\b", q, re.I)
    if m:
        return explain_exam(m.group(1).upper(), user)
    sids = sorted(st["timetable"] or {}, key=len, reverse=True)
    hit = next((sid for sid in sids if re.search(rf"\b{re.escape(sid)}\b", q, re.I)), None)
    if hit:
        return explain_session(hit, user)
    m = re.search(r"\b([A-Z]{2}\d{3})\b", q.upper())
    if m:
        cands = [sid for sid in sids if sid.startswith(m.group(1) + "-")]
        p = re.search(r"\bP(\d)\b|(\d{1,2})\s*(am|pm)", q, re.I)
        if p and cands:
            want = int(p.group(1)) if p.group(1) else (int(p.group(2)) - 7 if p.group(3).lower() == "am" else int(p.group(2)) + 4)
            cands = [sid for sid in cands if st["timetable"][sid][1] == want] or cands
        if cands:
            return explain_session(sorted(cands)[0], user)
    return {"facts": None, "source": "template",
            "text": "Ask about a session (e.g. 'Why is CS201-L1 at 8 AM?'), a course code, a booking (e.g. 'Why was BR-09 rejected?') or an exam (EX-MA101)."}


# ---------------------------------------------------------------- static frontend
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = _dist / path
        return FileResponse(f if path and f.is_file() else _dist / "index.html")
