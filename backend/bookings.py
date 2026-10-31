"""Spectra-week booking engine: priority rules, live evaluation, reasons.

All decisions are made here with guardrails.Occupancy; the LLM never sees a decision
until it is already made.
"""
from . import guardrails as G
from . import store
from .solver import request_options

LABEL = {"exam": "Exam", "regular_session": "Regular class", "institute_event": "Institute event",
         "guest_lecture": "Guest lecture", "department_event": "Department event", "club_event": "Club event"}
LAB_DEPTS = {"lab_cs": ["CSE"], "lab_elec": ["ECE", "EE"], "lab_phy": ["PH"], "workshop": ["ME"], "lab_civil": ["CE"]}


def period_label(data, a, b=None):
    per = {p["id"]: p for p in data["calendar"]["periods"]}
    b = a if b is None else b
    return f"P{a}-P{b} ({per[a]['start']}-{per[b]['end']})" if a != b else f"P{a} ({per[a]['start']}-{per[a]['end']})"


def rank(data, cat):
    return data["priority_order"].index(cat) + 1


def priority_text(data, cat):
    return f"{LABEL.get(cat, cat)}, priority {rank(data, cat)}"


def category_weights(data):
    """Validator weights (5/4/3/2) handed out in the Admin's current priority order."""
    events = [c for c in data["priority_order"] if c in G.CAT_WEIGHT]
    return dict(zip(events, sorted(G.CAT_WEIGHT.values(), reverse=True)))


def outranks(data, r1, r2):
    return G.V.outranks(data, r1, r2)


def lab_departments(data, room_id):
    room = next((r for r in data["rooms"] if r["id"] == room_id), None)
    return LAB_DEPTS.get(room["type"], []) if room else []


def _occupancy(st, ignore=()):
    return G.Occupancy(st["data"], store.week_assign(st), store.placed_bookings(st, ignore=ignore), ignore=ignore)


def _blockers(st, occ, req, rid, day, a):
    """Structured reasons one in-window placement fails: list of (kind, who, text)."""
    data, ctx = st["data"], occ.ctx
    out = []
    reqs = {r["id"]: r for r in st["requests"]}
    for p in range(a, a + req["duration_periods"]):
        if p > G.MAX_PERIOD:
            out.append(("time", None, f"P{p} does not exist (the last bookable period is P13, 21:00-22:00)"))
            break
        if (day, p) in ctx.room_un[rid]:
            out.append(("maintenance", None, f"{rid} is closed for maintenance {day} P{p}"))
        for who in occ.room[(rid, day, p)]:
            if who in ctx.sessions:
                s = ctx.sessions[who]
                out.append(("session", who, f"{rid} has {who} ({s['course_id']} {s['kind']}, {priority_text(data, 'regular_session')}) {day} {period_label(data, p)}"))
            elif who in reqs:
                o = reqs[who]["request"]
                cat = G.effective_category(data, o)
                out.append(("booking", who, f"{rid} is held by {who} '{o['title']}' ({priority_text(data, cat)}) {day} {period_label(data, p)}"))
        for g in req.get("mandatory_for_groups", []):
            for k in occ.grp[(g, day, p)]:
                out.append(("group", k, f"it is compulsory for {g}, who have {k} {day} {period_label(data, p)}"))
    seen, uniq = set(), []
    for b in out:
        if b[2] not in seen:
            seen.add(b[2])
            uniq.append(b)
    return uniq


def _in_window(req):
    for w in req["windows"]:
        for a in range(w["from_period"], w["to_period"] - req["duration_periods"] + 2):
            yield w["day"], a


def _sorted_rooms(data, req, requested=None):
    rooms = [r for r in data["rooms"] if G.room_fits_request(r, req)]
    rooms.sort(key=lambda r: (r["id"] != requested, r["capacity"]))
    return rooms


def static_problem(data, req):
    """Why no room could ever host this request, or None."""
    if any(G.room_fits_request(r, req) for r in data["rooms"]):
        return None
    typed = [r for r in data["rooms"] if r["type"] in req["room_types"] and "event" in r["allowed_uses"]]
    if not typed:
        return f"No bookable room is of type {', '.join(req['room_types'])}."
    big = max(typed, key=lambda r: r["capacity"])
    if big["capacity"] < req["attendance"]:
        return (f"No venue of type {', '.join(req['room_types'])} seats {req['attendance']}: the largest is "
                f"{big['id']} ({big['name']}, {big['capacity']} seats).")
    probs = "; ".join("; ".join(G.request_fit_problems(r, req)) for r in typed[:3])
    return f"No room meets every requirement ({probs})."


def window_problem(req):
    bad = [w for w in req["windows"]
           if w["to_period"] > G.MAX_PERIOD or w["to_period"] - w["from_period"] + 1 < req["duration_periods"]]
    if bad and len(bad) == len(req["windows"]):
        w = bad[0]
        if w["to_period"] > G.MAX_PERIOD:
            return (f"The requested window {w['day']} P{w['from_period']}-P{w['to_period']} runs past P13 "
                    f"(22:00), the last bookable period, so it cannot be approved as asked.")
        return (f"The requested window {w['day']} P{w['from_period']}-P{w['to_period']} is shorter than the "
                f"{req['duration_periods']} periods asked for.")
    return None


def approved_reason(data, req, rid, day, a):
    room = next(r for r in data["rooms"] if r["id"] == rid)
    b = a + req["duration_periods"] - 1
    extra = ""
    if req.get("requested_room") and req["requested_room"] != rid:
        known = any(r["id"] == req["requested_room"] for r in data["rooms"])
        extra = (f" Requested room {req['requested_room']} does not exist; {rid} is the closest match."
                 if not known else f" {req['requested_room']} was not usable, so {rid} was assigned.")
    feats = ", ".join(req["needs"]) if req["needs"] else "no special"
    return (f"Approved: Room {rid} is available on {day} {period_label(data, a, b)}, meets capacity "
            f"({req['attendance']}/{room['capacity']}) and feature requirements ({feats}).{extra}")


def explain_not_in_window(st, req, ignore_self=True):
    """Why no in-window placement works, citing the exact holders and their priority."""
    data = st["data"]
    sp = static_problem(data, req)
    if sp:
        return sp
    wp = window_problem(req)
    if wp:
        return wp
    occ = _occupancy(st, ignore=[req["id"]] if ignore_self else [])
    lines = []
    for r in _sorted_rooms(data, req, req.get("requested_room"))[:4]:
        for day, a in _in_window(req):
            bl = _blockers(st, occ, req, r["id"], day, a)
            if bl:
                lines.append(bl[0][2])
                break
    groups = sorted({b[2] for r in _sorted_rooms(data, req) for day, a in _in_window(req)
                     for b in _blockers(st, occ, req, r["id"], day, a) if b[0] == "group"})
    if groups:
        return "Every slot in the requested window clashes: " + groups[0] + "."
    uniq = list(dict.fromkeys(lines))
    if not uniq:
        return "No suitable room is free inside the requested window."
    return "No suitable room is free inside the requested window: " + "; ".join(uniq[:3]) + "."


def category_note(data, req):
    cat = G.effective_category(data, req)
    if req["requested_category"] == cat:
        return ""
    return (f" Requested as {LABEL.get(req['requested_category'], req['requested_category'])}, but a {req['role'].replace('_', ' ')} "
            f"request is capped at {priority_text(data, cat)}.")


def decision_reason(st, rec):
    data, req, pl = st["data"], rec["request"], rec["placement"]
    note = category_note(data, req)
    if rec["status"] == "approved":
        text = approved_reason(data, req, pl["room_id"], pl["day"], pl["start_period"])
        want = req.get("requested_room")
        if want and want != pl["room_id"] and any(r["id"] == want for r in data["rooms"]):
            occ = _occupancy(st, ignore=[req["id"]])
            room = next(r for r in data["rooms"] if r["id"] == want)
            bl = (_blockers(st, occ, req, want, pl["day"], pl["start_period"])
                  if G.room_fits_request(room, req) else [(None, None, "; ".join(G.request_fit_problems(room, req)))])
            if bl:
                text = text.replace(f" {want} was not usable, so {pl['room_id']} was assigned.",
                                    f" Requested {want} was not available ({bl[0][2]}), so {pl['room_id']} was assigned.")
        return text + note
    why = explain_not_in_window(st, req)
    if rec["status"] == "alternative_offered":
        return (f"{why} Alternative offered: {pl['room_id']} on {pl['day']} "
                f"{period_label(data, pl['start_period'], pl['end_period'])}.{note}")
    return why + ("" if static_problem(data, req) else " No alternative room or evening slot could be found.") + note


# ---------------------------------------------------------------- batch (dataset requests)
def apply_solver_decisions(st, decisions):
    """decisions: request id -> (room, day, start, inside) from the joint CP-SAT model."""
    for rec in st["requests"]:
        if rec["status"] == "cancelled" or rec["submitted_by"]:
            continue
        req = rec["request"]
        if rec["id"] in decisions:
            rid, day, a, inside = decisions[rec["id"]]
            rec["placement"] = {"room_id": rid, "day": day, "start_period": a, "end_period": a + req["duration_periods"] - 1}
            rec["status"] = "approved" if inside else "alternative_offered"
        else:
            rec["placement"], rec["status"] = None, "rejected"
        rec["decided_by"] = "allocation engine"
    for rec in st["requests"]:
        if rec["status"] in ("approved", "alternative_offered", "rejected") and not rec["submitted_by"]:
            rec["reason"] = decision_reason(st, rec)


# ---------------------------------------------------------------- live requests
def _relocate_session(st, occ_week, sid, banned_room, day, periods):
    """Week-7-only new home for a session displaced by an institute event."""
    data = st["data"]
    s = occ_week.ctx.sessions[sid]
    occ = G.Occupancy(data, store.week_assign(st), store.placed_bookings(st), ignore=[sid])
    for p in periods:   # the event itself will hold the room
        occ.room[(banned_room, day, p)].append("event")
    for d in occ.ctx.days:
        for t in G.valid_starts(occ.ctx, s):
            for r in sorted(data["rooms"], key=lambda r: r["capacity"]):
                if G.room_fits_session(r, s) and not occ.session_violations(s, d, t, r["id"]):
                    return {"session_id": sid, "day": d, "start_period": t, "room_id": r["id"]}
    return None


def evaluate(st, req):
    """Deterministic decision for a new request. Returns a proposal dict."""
    prop = _evaluate(st, req)
    prop["reason"] += category_note(st["data"], req)
    return prop


def _evaluate(st, req):
    data = st["data"]
    cat = G.effective_category(data, req)
    sp = static_problem(data, req)
    if sp:
        return {"status": "rejected", "placement": None, "reason": sp}
    occ = _occupancy(st, ignore=[req["id"]])
    prefs = [{"windows": req["windows"], "requested_room": req.get("requested_room")}] + req.get("fallbacks", [])
    notes = []
    for i, pref in enumerate(prefs):
        r2 = dict(req, windows=pref["windows"], requested_room=pref.get("requested_room"))
        rooms = _sorted_rooms(data, r2, pref.get("requested_room"))
        if len(prefs) > 1 and pref.get("requested_room"):
            # "X, or if busy then Y": each named room is a firm choice, not just a wish
            rooms = [r for r in rooms if r["id"] == pref["requested_room"]]
        # Case A: a clean in-window placement
        for day, a in _in_window(r2):
            for room in rooms:
                if not occ.booking_violations(r2, room["id"], day, a, a + req["duration_periods"] - 1):
                    pl = {"room_id": room["id"], "day": day, "start_period": a, "end_period": a + req["duration_periods"] - 1}
                    reason = approved_reason(data, r2, room["id"], day, a)
                    if i:
                        reason += " (fallback preference: " + " ".join(notes) + ")"
                    return {"status": "approved", "placement": pl, "reason": reason, "windows": pref["windows"]}
        # Case B: only lower-priority bookings (or, for institute events, movable classes) are in the way
        for day, a in _in_window(r2):
            for room in rooms:
                bl = _blockers(st, occ, r2, room["id"], day, a)
                if not bl or any(k in ("maintenance", "group", "time") for k, _, _ in bl):
                    continue
                held = {w for k, w, _ in bl if k == "booking"}
                sessions = {w for k, w, _ in bl if k == "session"}
                reqs = {r["id"]: r["request"] for r in st["requests"]}
                if any(not outranks(data, req, reqs[h]) for h in held):
                    continue
                overrides = []
                if sessions:
                    if rank(data, cat) > rank(data, "institute_event"):
                        continue
                    per = list(range(a, a + req["duration_periods"]))
                    overrides = [_relocate_session(st, occ, sid, room["id"], day, per) for sid in sessions]
                    if None in overrides:
                        continue
                pl = {"room_id": room["id"], "day": day, "start_period": a, "end_period": a + req["duration_periods"] - 1}
                what = [f"{h} ({priority_text(data, G.effective_category(data, reqs[h]))})" for h in sorted(held)]
                what += [f"{o['session_id']} moves to {o['day']} P{o['start_period']} in {o['room_id']} for Week 7 only" for o in overrides]
                return {"status": "needs_approval", "placement": pl, "displaces": sorted(held),
                        "overrides": [dict(o, displaced_by=req["id"]) for o in overrides], "windows": pref["windows"],
                        "reason": (f"{room['id']} {day} {period_label(data, a, pl['end_period'])} can be freed because this "
                                   f"{priority_text(data, cat)} outranks: " + "; ".join(what) + ". Needs Admin approval.")}
        notes.append(f"preference {i + 1} unavailable: {explain_not_in_window(st, r2)}")
    # Case C: alternative outside the window (same day first, then evenings)
    first = dict(req, windows=prefs[0]["windows"], requested_room=prefs[0].get("requested_room"))
    win_days = [w["day"] for w in first["windows"]]
    start0 = first["windows"][0]["from_period"] if first["windows"] else 10
    opts = [o for o in request_options(occ.ctx, first) if not o[3]]
    opts.sort(key=lambda o: (o[1] not in win_days, o[0] != first.get("requested_room"), abs(o[2] - start0),
                             next(r["capacity"] for r in data["rooms"] if r["id"] == o[0])))
    why = " ".join(notes) if len(prefs) > 1 else explain_not_in_window(st, first)
    for rid, day, a, _ in opts:
        b = a + req["duration_periods"] - 1
        if not occ.booking_violations(first, rid, day, a, b):
            return {"status": "alternative_offered", "placement": {"room_id": rid, "day": day, "start_period": a, "end_period": b},
                    "reason": f"{why} Alternative offered: {rid} on {day} {period_label(data, a, b)}."}
    return {"status": "rejected", "placement": None, "reason": why + " No alternative room or evening slot is free."}


def approver_rule(st, rec):
    """Which role may approve a pending request: 'admin' or 'coordinator:<DEPT>'."""
    if rec.get("displaces") or rec.get("overrides"):
        return "admin"
    depts = lab_departments(st["data"], (rec.get("placement") or {}).get("room_id"))
    return "coordinator:" + "/".join(depts) if depts else "admin"


def needs_human(st, rec, proposal):
    if proposal["status"] == "needs_approval":
        return True
    return proposal["status"] == "approved" and bool(lab_departments(st["data"], proposal["placement"]["room_id"]))
