"""Timetable orchestration: generation, staged changes with diffs, approvals, move options.

Every state change is checked by the official validator before it is committed
(`commit_guard`): a change may never add a hard violation.
"""
import copy

from . import bookings as B
from . import guardrails as G
from . import solver
from . import store


class GuardrailError(Exception):
    """A proposed change would break a hard constraint."""


def report(st, baseline_schedule=None):
    if not st["timetable"]:
        return None
    return G.official_report(st["data"], store.export_schedule(st), baseline_schedule)


def commit_guard(before, after):
    """Reject `after` if the official validator finds a hard violation that `before` did not have."""
    new = report(after)
    if new is None:
        return new
    old = {(v["rule"], v["detail"]) for v in (report(before) or {"hard_violations": []})["hard_violations"]}
    added = [v for v in new["hard_violations"] if (v["rule"], v["detail"]) not in old]
    if added:
        raise GuardrailError("; ".join(f"[{v['rule']}] {v['detail']}" for v in added[:5]))
    return new


def scoreboard(st):
    rep = report(st)
    if rep is None:
        return None
    moved = None
    if st["generated_schedule"]:
        base = {e["session_id"]: (e["day"], e["start_period"], e["room_id"]) for e in st["generated_schedule"]["sessions"]}
        moved = sum(1 for sid, v in store.assign_of(st).items() if sid in base and tuple(v) != base[sid])
    b = rep.get("bookings", {})
    return {"hard_violations": rep["hard_violation_count"], "hard_by_rule": rep["hard_by_rule"],
            "violations": rep["hard_violations"][:50], "soft_total": rep["soft_total"],
            "soft_penalties": rep["soft_penalties"], "booking_score": b.get("booking_score"),
            "booking_score_max": b.get("booking_score_max_if_all_approved"),
            "priority_inversions": b.get("priority_inversions", []),
            "exam_soft_penalty": rep.get("exam_soft_penalty"), "sessions_moved": moved,
            "warnings": rep["warnings"]}


# ---------------------------------------------------------------- generation
def generate(st, user, time_limit=90):
    """Fresh timetable + exams + Spectra decisions. Mutates st on success."""
    res = solver.solve_timetable(st["data"], time_limit=time_limit)
    if not res["ok"]:
        raise GuardrailError("No clash-free timetable exists: " + "; ".join(res["conflicts"]))
    ex = solver.solve_exams(st["data"])
    if not ex["ok"]:
        raise GuardrailError("; ".join(ex["conflicts"]))
    before = copy.deepcopy(st)
    st["timetable"] = {sid: list(v) for sid, v in res["assign"].items()}
    st["exams"] = ex["exams"]
    st["session_overrides"] = []
    st["pending"] = []
    B.apply_solver_decisions(st, res["bookings"])
    rep = report(st)
    if rep["hard_violation_count"]:
        st.clear()
        st.update(before)
        raise GuardrailError("Validator rejected the generated timetable: " +
                             "; ".join(v["detail"] for v in rep["hard_violations"][:5]))
    st["generated_schedule"] = store.export_schedule(st)
    st["history"] = []
    store.audit(st, user, "generate", f"Timetable generated ({res['status']}, {res['wall_time']:.0f}s): "
                f"0 hard violations, soft {rep['soft_total']}, booking score {rep['bookings']['booking_score']}")
    return rep


# ---------------------------------------------------------------- diffs and staging
def diff(st_data, before, after, data_after):
    sess = {s["id"]: s for s in data_after["sessions"]}
    old_sess = {s["id"]: s for s in st_data["sessions"]}
    moved, added, removed = [], [], []
    for sid, v in after.items():
        s = sess[sid]
        row = {"session_id": sid, "course_id": s["course_id"], "kind": s["kind"], "faculty_id": s["faculty_id"],
               "groups": s["groups"], "new": {"day": v[0], "start_period": v[1], "room_id": v[2]}}
        if sid not in before:
            added.append(row)
        elif tuple(before[sid]) != tuple(v):
            o = before[sid]
            row["old"] = {"day": o[0], "start_period": o[1], "room_id": o[2]}
            row["change"] = "room only" if tuple(o[:2]) == tuple(v[:2]) else "time"
            moved.append(row)
    for sid, o in before.items():
        if sid not in after:
            s = old_sess.get(sid, {})
            removed.append({"session_id": sid, "course_id": s.get("course_id"), "kind": s.get("kind"),
                            "old": {"day": o[0], "start_period": o[1], "room_id": o[2]}})
    return {"moved": moved, "added": added, "removed": removed}


def describe_change(ch):
    t = ch["type"]
    if t == "faculty_unavailable":
        return f"{ch['faculty_id']} unavailable {', '.join(ch['days'])} P{min(ch['periods'])}-P{max(ch['periods'])}"
    if t == "room_unavailable":
        return f"{ch['room_id']} closed {', '.join(ch['days'])} P{min(ch['periods'])}-P{max(ch['periods'])}"
    if t == "add_session":
        return f"new session {ch['session']['id']} ({ch['session']['course_id']}, {ch['session']['faculty_id']})"
    if t == "add_faculty":
        return f"new faculty {ch['faculty']['id']}"
    if t == "remove_session":
        return f"remove session {ch['session_id']}"
    return t


def _released_bookings(st, data_after):
    """Bookings whose room is now closed during their slot."""
    ctx = G.V.Ctx(data_after)
    out = []
    for b in store.placed_bookings(st):
        if any((b["day"], p) in ctx.room_un[b["room_id"]] for p in range(b["start_period"], b["end_period"] + 1)):
            out.append(b["request_id"])
    return out


def _exam_conflicts(st, data_after):
    ctx = G.V.Ctx(data_after)
    bad = []
    for e in st["exams"] or []:
        for rid in e["rooms"]:
            if any((e["day"], p) in ctx.room_un[rid] for p in G.EXAM_SESSION_PERIODS.get(e["session"], [])):
                bad.append(e["exam_id"])
    return bad


def propose(st, changes, user, source, requested_by=None, explicit=None, time_limit=60):
    """Stage a change. `changes` uses the validator change format. `explicit` (sid -> slot)
    skips the solver for a hand-picked, already-validated move. Returns the pending item."""
    if not st["timetable"]:
        raise GuardrailError("Generate the timetable first.")
    try:
        data_after = G.V.apply_disruption(st["data"], {"changes": changes})
    except (KeyError, ValueError) as e:
        raise GuardrailError(f"Change file could not be applied: {e}")
    before = store.assign_of(st)
    released = _released_bookings(st, data_after)
    blockers = store.placed_bookings(st, ignore=released)
    item = {"id": store.new_id("CHG"), "created_at": store.now(), "created_by": user, "requested_by": requested_by,
            "source": source, "changes": changes, "change_summary": [describe_change(c) for c in changes],
            "status": "pending", "released_bookings": released}
    if explicit is not None:
        after = dict(before)
        after.update({k: tuple(v) for k, v in explicit.items()})
        status = "MANUAL"
    else:
        removed = {c["session_id"] for c in changes if c["type"] == "remove_session"}
        base = {k: v for k, v in before.items() if k not in removed}
        res = solver.solve_reschedule(data_after, base, blockers, time_limit=time_limit)
        if not res["ok"]:
            item.update(status="infeasible", conflicts=res["conflicts"], solver_status=res["status"])
            st["pending"].insert(0, item)
            return item
        after, status = res["assign"], res["status"]
    trial = copy.deepcopy(st)
    trial["data"] = data_after
    trial["timetable"] = {k: list(v) for k, v in after.items()}
    for rid in released:
        rec = next(r for r in trial["requests"] if r["id"] == rid)
        rec["status"], rec["placement"] = "rejected", None
    rep_after = report(trial)
    item.update(solver_status=status, proposed_timetable=trial["timetable"],
                diff=diff(st["data"], before, after, data_after),
                exam_conflicts=_exam_conflicts(st, data_after),
                score_before=_score_view(report(st)), score_after=_score_view(rep_after))
    item["moved_count"] = len(item["diff"]["moved"])
    if rep_after["hard_violation_count"] > (report(st) or {}).get("hard_violation_count", 0):
        item.update(status="infeasible",
                    conflicts=[f"[{v['rule']}] {v['detail']}" for v in rep_after["hard_violations"][:10]])
    st["pending"].insert(0, item)
    return item


def _score_view(rep):
    if not rep:
        return None
    return {"hard": rep["hard_violation_count"], "soft": rep["soft_total"],
            "booking": rep.get("bookings", {}).get("booking_score")}


def approve(st, item_id, user):
    item = next((p for p in st["pending"] if p["id"] == item_id), None)
    if item is None or item["status"] != "pending":
        raise GuardrailError("This change is not waiting for approval.")
    before = copy.deepcopy(st)
    data_after = G.V.apply_disruption(st["data"], {"changes": item["changes"]})
    st["history"].insert(0, {"id": item["id"], "timetable": st["timetable"], "changes": item["changes"], "at": store.now()})
    st["data"] = data_after
    st["changes"] += item["changes"]
    st["timetable"] = item["proposed_timetable"]
    for rid in item["released_bookings"]:
        rec = next(r for r in st["requests"] if r["id"] == rid)
        rec["status"], rec["placement"] = "rejected", None
        prop = B.evaluate(st, rec["request"])
        if prop["status"] in ("approved", "alternative_offered"):
            rec["status"], rec["placement"] = prop["status"], prop["placement"]
        rec["reason"] = f"Venue closed by a timetable change ({', '.join(item['change_summary'])}). " + prop["reason"]
    if item["exam_conflicts"]:
        ex = solver.solve_exams(st["data"])
        if ex["ok"]:
            st["exams"] = ex["exams"]
    # overrides whose session moved are re-derived from scratch next time; drop stale ones
    st["session_overrides"] = [o for o in st["session_overrides"] if o["session_id"] in st["timetable"]]
    try:
        commit_guard(before, st)
    except GuardrailError:
        st.clear()
        st.update(before)
        raise
    item["status"] = "approved"
    item["decided_by"] = user
    item["decided_at"] = store.now()
    store.audit(st, user, "approve-change", f"{item['id']}: {item['moved_count']} sessions moved")
    return item


def reject(st, item_id, user, note=""):
    item = next((p for p in st["pending"] if p["id"] == item_id), None)
    if item is None or item["status"] not in ("pending", "infeasible"):
        raise GuardrailError("This change is not open.")
    item.update(status="rejected", decided_by=user, decided_at=store.now(), note=note)
    store.audit(st, user, "reject-change", item["id"])
    return item


# ---------------------------------------------------------------- single-session moves
def soft_delta(st, assign, sid, slot):
    ctx = G.V.Ctx(st["data"])
    _, base = G.V.soft_score(ctx, assign)
    trial = dict(assign)
    trial[sid] = slot
    _, after = G.V.soft_score(ctx, trial)
    return after - base


def move_options(st, session, days=None, periods=None, limit=8):
    """Clash-free (day, start, room) for one session, best soft penalty first."""
    data = st["data"]
    sid = session["id"]
    assign = store.assign_of(st)
    if session.get("basket_slot"):
        return [], [f"{sid} is in elective basket {session['basket_slot']}; all three electives must move together, "
                    f"so this needs a full reschedule (ask the Admin)."]
    occ = G.Occupancy(data, assign, store.placed_bookings(st), ignore=[sid])
    opts, blocked = [], {}
    for d in (days or occ.ctx.days):
        for t in G.valid_starts(occ.ctx, session):
            per = G.V.block(t, session["duration"])
            if periods and not set(per) <= set(periods):
                continue
            if sid in assign and assign[sid][:2] == (d, t):
                continue
            best = None
            first_reason = None
            for r in sorted(data["rooms"], key=lambda r: r["capacity"]):
                if not G.room_fits_session(r, session):
                    continue
                v = occ.session_violations(session, d, t, r["id"])
                if not v:
                    best = r["id"]
                    break
                first_reason = first_reason or v
            if best:
                opts.append({"day": d, "start_period": t, "room_id": best,
                             "soft_delta": soft_delta(st, assign, sid, (d, t, best)) if sid in assign else 0})
            else:
                # name what blocks the slot itself (not just the first room tried)
                time_only = [x for x in (first_reason or ["no suitable room"]) if not x.startswith(("H4", "H5", "H6", "H8", "H10"))]
                blocked[f"{d} P{t}"] = (time_only or first_reason or ["every suitable room is taken"])[0]
    opts.sort(key=lambda o: (o["soft_delta"], o["day"], o["start_period"]))
    return opts[:limit], [f"{k}: {v}" for k, v in list(blocked.items())[:12]]


# ---------------------------------------------------------------- explanations
def explain_session(st, sid):
    data = st["data"]
    assign = store.assign_of(st)
    ctx = G.V.Ctx(data)
    s = ctx.sessions[sid]
    d, t, rid = assign[sid]
    occ = G.Occupancy(data, assign, store.placed_bookings(st), ignore=[sid])
    feasible, blocked = [], {}
    for dd in ctx.days:
        for tt in G.valid_starts(ctx, s):
            if (dd, tt) == (d, t):
                continue
            room_ok = None
            reasons = None
            for r in data["rooms"]:
                if not G.room_fits_session(r, s):
                    continue
                v = occ.session_violations(s, dd, tt, r["id"])
                if not v:
                    room_ok = r["id"]
                    break
                reasons = reasons or v
            if room_ok:
                feasible.append((soft_delta(st, assign, sid, (dd, tt, room_ok)), dd, tt))
            else:
                rule = (reasons or ["H4 every fitting room is taken"])[0].split(":")[0]
                blocked.setdefault(rule, []).append(f"{dd} P{tt}")
    rule_names = {"H2": "the faculty member is already teaching", "H3": "a student group already has a class",
                  "H4": "every fitting room is taken", "H7": "the faculty member is unavailable",
                  "H8": "rooms are closed", "H11": "the elective basket is fixed at another time",
                  "H12": "the faculty member would exceed the daily limit", "H13": "a compulsory event is booked",
                  "H9": "lunch/lab start rules", "H14": "the session is fixed"}
    room = ctx.rooms[rid]
    fitting = sorted([r for r in data["rooms"] if G.room_fits_session(r, s)], key=lambda r: r["capacity"])
    facts = {
        "session": sid, "course": s["course_id"], "kind": s["kind"], "faculty": s["faculty_id"], "groups": s["groups"],
        "slot": f"{d} {B.period_label(data, t, t + s['duration'] - 1)}", "room": rid,
        "room_reason": (f"{rid} seats {room['capacity']} for {s['size']} students; it is the "
                        f"{'smallest' if fitting and fitting[0]['id'] == rid else 'a'} free room of type "
                        f"{'/'.join(s['room_types'])}" + (" with wheelchair access" if s.get("needs_accessible") else "")
                        + ("" if s["size"] / room["capacity"] >= 0.4 else " (under 40% full: no smaller room was free)")),
        "blocked": {rule_names.get(k, k): len(v) for k, v in blocked.items()},
        "blocked_examples": {rule_names.get(k, k): v[:4] for k, v in blocked.items()},
        "feasible_alternatives": len(feasible),
        "cheaper_or_equal_alternatives": sorted([f"{dd} P{tt}" for c, dd, tt in feasible if c <= 0])[:5],
        "costlier_alternatives": [f"{dd} P{tt} (+{c} soft penalty)" for c, dd, tt in sorted(feasible)[:5] if c > 0],
    }
    return facts


def explain_text(facts):
    """Deterministic fallback wording for explain_session facts."""
    lines = [f"{facts['session']} ({facts['course']} {facts['kind']}, {facts['faculty']}, groups {', '.join(facts['groups'])}) "
             f"is at {facts['slot']} in {facts['room']}."]
    if facts["blocked"]:
        parts = [f"{n} slot(s) because {why}" for why, n in sorted(facts["blocked"].items(), key=lambda x: -x[1])]
        lines.append("Other slots are ruled out by hard constraints: " + "; ".join(parts) + ".")
    if facts["costlier_alternatives"]:
        lines.append("The remaining legal slots would raise the soft penalty: " + ", ".join(facts["costlier_alternatives"]) + ".")
    if facts["cheaper_or_equal_alternatives"]:
        lines.append("Equally good legal slots also exist (" + ", ".join(facts["cheaper_or_equal_alternatives"]) +
                     "); the solver picked this one while optimising the whole week together.")
    lines.append("Room: " + facts["room_reason"] + ".")
    return " ".join(lines)
