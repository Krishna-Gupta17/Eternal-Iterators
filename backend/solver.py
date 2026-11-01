"""CP-SAT models for the weekly timetable, Spectra-week bookings, rescheduling and exams.

Every constraint here mirrors a rule in validator.py; every solution is re-checked by
the official validator (guardrails.official_report) before it is stored.
"""
import os
import time
from collections import defaultdict

from ortools.sat.python import cp_model

from . import guardrails as G

SLOTS_PER_DAY = 13            # global time index = day_index * 13 + (period - 1)
MOVE_WEIGHT = 1000            # one moved session costs more than any soft-penalty saving
BOOK_WEIGHT = 100             # per half booking point (approved = 2 x category weight)
RESCHEDULE_TIME_LIMIT = float(os.getenv("RESCHEDULE_TIME_LIMIT", "10"))


def default_workers():
    """At least 8 CP-SAT workers, more on bigger machines (container-aware on Linux).
    Below 4 workers CP-SAT drops its LNS portfolio and the reschedule stalls: pinned to a
    single core, 8 time-sliced workers reach the optimum in ~1.5s while 1 worker is still
    20 moves off after 10s. SOLVER_WORKERS overrides the default."""
    if os.getenv("SOLVER_WORKERS"):
        return max(1, int(os.environ["SOLVER_WORKERS"]))
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:
        n = os.cpu_count() or 1
    return max(8, min(16, n))


def _avoid_cost(ctx, s, day, per):
    cost = 0
    for pref in ctx.fac.get(s["faculty_id"], {}).get("preferences", []):
        if pref.get("type") == "avoid" and day in pref.get("days", ctx.days):
            cost += 2 * sum(1 for p in per if p in pref["periods"])
    return cost


def request_options(ctx, req, occupied_rooms=None):
    """All (room, day, start, in_window) placements that pass the time-independent checks.
    In-window placements can be approved; the rest can only be offered as alternatives."""
    dur = req["duration_periods"]
    last_period = G.LAB_LAST_PERIOD if G.is_lab_request(req) else G.MAX_PERIOD
    rooms = [r for r in ctx.data["rooms"] if G.room_fits_request(r, req)]
    win_days = {w["day"] for w in req["windows"]}
    out = []
    for r in rooms:
        for day in ctx.days:
            for a in range(1, last_period - dur + 2):
                per = range(a, a + dur)
                if any((day, p) in ctx.room_un[r["id"]] for p in per):
                    continue
                if occupied_rooms and any(occupied_rooms.get((r["id"], day, p)) for p in per):
                    continue
                inside = any(w["day"] == day and w["from_period"] <= a and a + dur - 1 <= w["to_period"]
                             for w in req["windows"])
                # alternatives are kept to the requested days and to evening slots, which is
                # what a requester can actually use and keeps the model small
                if not inside and day not in win_days and a < 10:
                    continue
                out.append((r["id"], day, a, inside))
    return out


class TimetableModel:
    """Builds one CP-SAT model.

    requests  : booking requests decided jointly with the timetable (fresh generation)
    blockers  : already-approved bookings that must stay put (rescheduling)
    baseline  : sid -> (day, start, room); adds the minimum-perturbation objective
    diagnose  : gate relaxable constraint groups behind assumption literals
    freeze    : sid -> (day, start, room); those sessions only get variables for that slot
    """

    def __init__(self, data, requests=(), blockers=(), baseline=None, diagnose=False, freeze=None):
        self.data = data
        self.ctx = ctx = G.V.Ctx(data)
        freeze = freeze or {}
        self.m = m = cp_model.CpModel()
        self.diagnose = diagnose
        self.gates = []                       # (literal, description)
        self.problems = []                    # structural impossibilities found while building
        self.sessions = list(data["sessions"])
        di = {d: i for i, d in enumerate(ctx.days)}

        def gate(desc):
            if not diagnose:
                return None
            lit = m.NewBoolVar(desc)
            self.gates.append((lit, desc))
            return lit

        # ---------------------------------------------------------------- time + room vars
        self.T = {}          # (sid, day, start) -> bool
        self.Y = {}          # (sid, room) -> bool
        self.start = {}
        self.TD = defaultdict(list)          # (sid, day) -> [(start, T literal)]
        cover = defaultdict(list)            # (sid, day, p) -> T literals covering p
        obj = []
        fac_un_gate = {}
        for f in data["faculty"]:
            for i, u in enumerate(f.get("unavailable", [])):
                lit = gate(f"{f['id']} unavailable {u['day']} P{min(u['periods'])}-P{max(u['periods'])}"
                           + (f" ({u['reason']})" if u.get("reason") else ""))
                for p in u["periods"]:
                    fac_un_gate.setdefault((f["id"], u["day"], p), []).append(lit)

        # rooms closed for every teaching period get no room variables at all
        closed_all_week = {rid for rid in ctx.rooms
                           if all((d, p) in ctx.room_un[rid] for d in ctx.days for p in range(1, G.LAST_TEACHING + 1))}
        for s in self.sessions:
            sid = s["id"]
            rooms = [r for r in data["rooms"] if G.room_fits_session(r, s) and r["id"] not in closed_all_week]
            starts = G.valid_starts(ctx, s)
            days = G.valid_days(ctx, s)
            if sid in freeze:
                fd, ft, fr = freeze[sid]
                days, starts = [d for d in days if d == fd], [t for t in starts if t == ft]
                rooms = [r for r in rooms if r["id"] == fr]
            if not rooms:
                self.problems.append(f"{sid}: no room is of type {'/'.join(s['room_types'])} with "
                                     f"{s['size']}+ seats" + (" and wheelchair access" if s.get("needs_accessible") else ""))
            if not starts or not days:
                self.problems.append(f"{sid}: its fixed slot {s.get('fixed_slot')} is not a legal start "
                                     f"(lunch break, lab start rule or the {G.ACADEMIC_CUTOFF} cutoff for classes)")
            sched = gate(f"session {sid} ({s['course_id']}, {s['faculty_id']}) must be scheduled")
            tl = []
            for d in days:
                for t in starts:
                    per = G.V.block(t, s["duration"])
                    hard_blocked = [(d, p) for p in per if (d, p) in ctx.fac_un[s["faculty_id"]]]
                    if hard_blocked and not diagnose:
                        continue
                    v = m.NewBoolVar(f"T_{sid}_{d}_{t}")
                    self.T[(sid, d, t)] = v
                    self.TD[(sid, d)].append((t, v))
                    tl.append(v)
                    for (dd, p) in hard_blocked:
                        for lit in fac_un_gate[(s["faculty_id"], dd, p)]:
                            m.Add(v == 0).OnlyEnforceIf(lit)
                    for p in per:
                        cover[(sid, d, p)].append(v)
                    c = _avoid_cost(ctx, s, d, per) + (1 if G.LAST_TEACHING in per else 0)
                    if c:
                        obj.append(c * v)
            yl = []
            for r in rooms:
                y = m.NewBoolVar(f"Y_{sid}_{r['id']}")
                self.Y[(sid, r["id"])] = y
                yl.append(y)
                if s["size"] / r["capacity"] < 0.4:
                    obj.append(y)
            if sched is None:
                m.AddExactlyOne(tl)
                m.AddExactlyOne(yl)
            else:
                m.Add(sum(tl) == 1).OnlyEnforceIf(sched)
                m.Add(sum(yl) == 1).OnlyEnforceIf(sched)
                m.Add(sum(tl) <= 1)
                m.Add(sum(yl) <= 1)
            st = m.NewIntVar(0, len(ctx.days) * SLOTS_PER_DAY, f"start_{sid}")
            m.Add(st == sum((di[d] * SLOTS_PER_DAY + t - 1) * v for d in days for t, v in self.TD[(sid, d)]))
            self.start[sid] = st

        # ---------------------------------------------------------------- rooms (H4, H8, H13)
        room_iv = defaultdict(list)
        for (sid, rid), y in self.Y.items():
            s = ctx.sessions[sid]
            room_iv[rid].append(m.NewOptionalFixedSizeIntervalVar(self.start[sid], s["duration"], y, f"iv_{sid}_{rid}"))
        for u_i, u in enumerate(data.get("room_unavailability", [])):
            lit = gate(f"{u['room_id']} closed {u['day']} P{min(u['periods'])}-P{max(u['periods'])}"
                       + (f" ({u['reason']})" if u.get("reason") else ""))
            for p in u["periods"]:
                g0 = di[u["day"]] * SLOTS_PER_DAY + p - 1
                room_iv[u["room_id"]].append(
                    m.NewOptionalFixedSizeIntervalVar(g0, 1, lit, "ru") if lit is not None
                    else m.NewFixedSizeIntervalVar(g0, 1, "ru"))

        grp_extra = defaultdict(list)        # (g, day, p) -> literals of mandatory bookings
        for b in blockers:
            lit = gate(f"approved booking {b['request_id']} in {b['room_id']} {b['day']} P{b['start_period']}-P{b['end_period']}")
            g0 = di[b["day"]] * SLOTS_PER_DAY + b["start_period"] - 1
            size = b["end_period"] - b["start_period"] + 1
            room_iv[b["room_id"]].append(
                m.NewOptionalFixedSizeIntervalVar(g0, size, lit, "bk") if lit is not None
                else m.NewFixedSizeIntervalVar(g0, size, "bk"))
            for g in b.get("mandatory_for_groups", []):
                for p in range(b["start_period"], b["end_period"] + 1):
                    grp_extra[(g, b["day"], p)].append(1 if lit is None else lit)

        self.req_opts = {}
        book_obj = []
        for req in requests:
            opts = []
            for (rid, day, a, inside) in request_options(ctx, req):
                lit = m.NewBoolVar(f"B_{req['id']}_{rid}_{day}_{a}")
                opts.append((lit, rid, day, a, inside))
                dur = req["duration_periods"]
                room_iv[rid].append(m.NewOptionalFixedSizeIntervalVar(
                    di[day] * SLOTS_PER_DAY + a - 1, dur, lit, "bopt"))
                for g in req["mandatory_for_groups"]:
                    for p in range(a, a + dur):
                        grp_extra[(g, day, p)].append(lit)
                w = G.CAT_WEIGHT[G.effective_category(data, req)]
                gain = (2 * w if inside else w) * BOOK_WEIGHT
                # tie-breaks well below one booking point: requested room, then the alternative
                # closest to what was asked for (same day, nearest start)
                near = 0 if inside else min((abs(a - w["from_period"]) + (0 if w["day"] == day else 20)
                                             for w in req["windows"]), default=20)
                bonus = (1 if rid == req.get("requested_room") else 0) - near
                book_obj.append((gain + bonus) * lit)
            if opts:
                m.AddAtMostOne(o[0] for o in opts)
            self.req_opts[req["id"]] = opts
        for rid, ivs in room_iv.items():
            m.AddNoOverlap(ivs)

        # ---------------------------------------------------------------- faculty (H2, H12)
        by_fac = defaultdict(list)
        for s in self.sessions:
            by_fac[s["faculty_id"]].append(s)
        for f, ss in by_fac.items():
            mx = ctx.fac.get(f, {}).get("max_hours_per_day", 4)
            lit_o = gate(f"{f} cannot teach two sessions at once")
            lit_l = gate(f"{f} teaches at most {mx}h per day")
            for d in ctx.days:
                for p in range(1, G.LAST_TEACHING + 1):
                    terms = [v for s in ss for v in cover[(s["id"], d, p)]]
                    if len(terms) > 1:
                        c = m.Add(sum(terms) <= 1)
                        if lit_o is not None:
                            c.OnlyEnforceIf(lit_o)
                hours = [s["duration"] * v for s in ss for _, v in self.TD[(s["id"], d)]]
                if sum(x["duration"] for x in ss) > mx and hours:
                    c = m.Add(sum(hours) <= mx)
                    if lit_l is not None:
                        c.OnlyEnforceIf(lit_l)

        # ---------------------------------------------------------------- baskets (H11)
        baskets = defaultdict(list)
        for s in self.sessions:
            if s.get("basket_slot"):
                baskets[s["basket_slot"]].append(s["id"])
        for b, sids in baskets.items():
            lit = gate(f"all sessions of elective basket {b} run at the same time")
            for other in sids[1:]:
                for key in set(k for k in self.T if k[0] in (sids[0], other)):
                    d, t = key[1], key[2]
                    a = self.T.get((sids[0], d, t), 0)
                    c = self.T.get((other, d, t), 0)
                    con = m.Add(a == c)
                    if lit is not None:
                        con.OnlyEnforceIf(lit)

        # ---------------------------------------------------------------- groups (H3) + soft
        rep = defaultdict(list)            # group -> sessions as the group sees them (one per basket slot)
        seen = set()
        for s in self.sessions:
            for g in s["groups"]:
                if s.get("basket_slot"):
                    if (g, s["basket_slot"]) in seen:
                        continue
                    seen.add((g, s["basket_slot"]))
                rep[g].append(s)
        groups = set(rep) | {g for (g, _, _) in grp_extra}
        for g in groups:
            lit = gate(f"group {g} cannot attend two things at once")
            for d in ctx.days:
                occ = []
                for p in range(1, G.MAX_PERIOD + 1):
                    terms = [v for s in rep[g] for v in cover[(s["id"], d, p)]]
                    extra = grp_extra.get((g, d, p), [])
                    if len(terms) + len(extra) > 1:
                        c = m.Add(sum(terms) + sum(extra) <= 1)
                        if lit is not None:
                            c.OnlyEnforceIf(lit)
                    if p <= G.LAST_TEACHING:
                        occ.append(sum(terms) if terms else 0)
                if diagnose:
                    continue
                # S2: free period between two classes. before/after are prefix/suffix ORs.
                before = [None] + [m.NewBoolVar("") for _ in range(1, G.LAST_TEACHING)]
                after = [m.NewBoolVar("") for _ in range(G.LAST_TEACHING - 1)] + [None]
                for i in range(1, G.LAST_TEACHING):
                    m.Add(before[i] >= occ[i - 1])
                    if i > 1:
                        m.Add(before[i] >= before[i - 1])
                for i in range(G.LAST_TEACHING - 2, -1, -1):
                    m.Add(after[i] >= occ[i + 1])
                    if i < G.LAST_TEACHING - 2:
                        m.Add(after[i] >= after[i + 1])
                for i in range(1, G.LAST_TEACHING - 1):
                    gap = m.NewBoolVar("")
                    m.Add(gap >= before[i] + after[i] - 1 - occ[i])
                    obj.append(gap)
                # S3: more than 6 hours in a day
                hrs = [s["duration"] * v for s in rep[g] for _, v in self.TD[(s["id"], d)]]
                if sum(s["duration"] for s in rep[g]) > 6:
                    ex = m.NewIntVar(0, 9, "")
                    m.Add(ex >= sum(hrs) - 6)
                    obj.append(3 * ex)
                # S4: two lectures of the same course on one day
                by_course = defaultdict(list)
                for s in rep[g]:
                    if s["kind"] == "lecture" and not s.get("basket_slot"):
                        by_course[s["course_id"]].append(s)
                for cid, ss in by_course.items():
                    if len(ss) > 1:
                        cnt = [v for s in ss for _, v in self.TD[(s["id"], d)]]
                        ex = m.NewIntVar(0, len(ss), "")
                        m.Add(ex >= sum(cnt) - 1)
                        obj.append(3 * ex)
        if not diagnose:
            by_course = defaultdict(list)
            for s in self.sessions:
                if s.get("basket_slot"):
                    by_course[s["course_id"]].append(s)
            for cid, ss in by_course.items():
                for d in ctx.days:
                    cnt = [v for s in ss for _, v in self.TD[(s["id"], d)]]
                    if len(ss) > 1 and cnt:
                        ex = m.NewIntVar(0, len(ss), "")
                        m.Add(ex >= sum(cnt) - 1)
                        obj.append(3 * ex)

        # ---------------------------------------------------------------- stability
        self.stay = {}
        if baseline and not diagnose:
            for sid, (d, t, rid) in baseline.items():
                tv, yv = self.T.get((sid, d, t)), self.Y.get((sid, rid))
                if tv is None or yv is None:
                    continue            # old slot is no longer legal: the session must move
                stay = m.NewBoolVar(f"stay_{sid}")
                m.Add(stay <= tv)
                m.Add(stay <= yv)
                self.stay[sid] = stay
                m.AddHint(stay, 1)
            # warm start: the old timetable as a complete hint (every T/Y of a baselined
            # session, 0s included), so CP-SAT repairs it instead of searching from scratch
            for (sid, d, t), v in self.T.items():
                if sid in baseline:
                    m.AddHint(v, (d, t) == tuple(baseline[sid][:2]))
            for (sid, rid), y in self.Y.items():
                if sid in baseline:
                    m.AddHint(y, rid == baseline[sid][2])
            moved_possible = [sid for sid in baseline if sid in ctx.sessions]
            obj.append(MOVE_WEIGHT * (len(moved_possible) - sum(self.stay.values())))

        if not diagnose:
            m.Minimize(sum(obj) - sum(book_obj))

    # -------------------------------------------------------------------- solving
    def solve(self, time_limit=60, workers=None, light_presolve=False):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = workers or default_workers()
        solver.parameters.random_seed = 7
        if light_presolve:
            # presolve probing costs ~4s per pass on the full week and buys little when
            # the search starts from a complete hint
            solver.parameters.cp_model_probing_level = 0
        status = solver.Solve(self.m)
        self.status = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        assign = {}
        for (sid, d, t), v in self.T.items():
            if solver.Value(v):
                rid = next(r["id"] for r in self.data["rooms"]
                           if (sid, r["id"]) in self.Y and solver.Value(self.Y[(sid, r["id"])]))
                assign[sid] = (d, t, rid)
        bookings = {}
        for rid_, opts in self.req_opts.items():
            for lit, rid, day, a, inside in opts:
                if solver.Value(lit):
                    bookings[rid_] = (rid, day, a, inside)
        return {"assign": assign, "bookings": bookings, "status": self.status,
                "objective": solver.ObjectiveValue(), "wall_time": solver.WallTime()}

    def conflicting_constraints(self, time_limit=30):
        """Run with every gate as an assumption and return a sufficient infeasible subset."""
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 1
        solver.parameters.cp_model_probing_level = 0      # same core in ~2s instead of ~10s
        self.m.AddAssumptions([lit for lit, _ in self.gates])
        status = solver.Solve(self.m)
        if status != cp_model.INFEASIBLE:
            return None
        core = set(solver.SufficientAssumptionsForInfeasibility())
        return [desc for lit, desc in self.gates if lit.Index() in core]


# ------------------------------------------------------------------------ public API
def solve_timetable(data, time_limit=90):
    """Fresh timetable + Spectra decisions for every non-cancelled request."""
    cancelled = {c["request_id"] for c in data.get("cancellations", [])}
    reqs = [r for r in data["booking_requests"] if r["id"] not in cancelled]
    model = TimetableModel(data, requests=reqs)
    if model.problems:
        return {"ok": False, "status": "INFEASIBLE", "conflicts": model.problems}
    res = model.solve(time_limit=time_limit)
    if res is None:
        return {"ok": False, "status": model.status, "conflicts": diagnose(data)}
    res["ok"] = True
    return res


def displaced_sessions(data, baseline, blockers=()):
    """Sessions whose old slot is illegal under `data` (or that have no slot yet)."""
    ctx = G.V.Ctx(data)
    booked = {(b["room_id"], b["day"], p) for b in blockers for p in range(b["start_period"], b["end_period"] + 1)}
    out = {sid for sid in ctx.sessions if sid not in baseline}
    for sid, (d, t, rid) in baseline.items():
        s, r = ctx.sessions.get(sid), ctx.rooms.get(rid)
        if s is None:
            continue
        per = G.V.block(t, s["duration"])
        if (r is None or not G.room_fits_session(r, s) or d not in G.valid_days(ctx, s)
                or t not in G.valid_starts(ctx, s)
                or any((d, p) in ctx.fac_un[s["faculty_id"]] or (d, p) in ctx.room_un[rid]
                       or (rid, d, p) in booked for p in per)):
            out.add(sid)
    return out


def ripple_scope(data, displaced):
    """Displaced sessions plus every session sharing a faculty member or a student group
    with one: the sessions whose time slots can make room for the displaced ones.
    Elective baskets are taken whole because their sessions move together."""
    sessions = data["sessions"]
    fac = {s["faculty_id"] for s in sessions if s["id"] in displaced}
    grp = {g for s in sessions if s["id"] in displaced for g in s["groups"]}
    scope = set(displaced) | {s["id"] for s in sessions if s["faculty_id"] in fac or grp & set(s["groups"])}
    baskets = {s["basket_slot"] for s in sessions if s["id"] in scope and s.get("basket_slot")}
    return scope | {s["id"] for s in sessions if s.get("basket_slot") in baskets}


def solve_reschedule(data, baseline, blockers, time_limit=RESCHEDULE_TIME_LIMIT):
    """Minimum-perturbation repair: Minimize(MOVE_WEIGHT * moved + soft penalty).
    `data` already has the disruption applied; `blockers` are approved bookings.

    Tries the ripple neighbourhood first (everything else frozen at its old slot), then
    the whole week, both inside one time budget; the best FEASIBLE repair is returned if
    the budget runs out. No gap limit: the lower bound is tight from the start, so proving
    optimality is cheap, and a relative gap on MOVE_WEIGHT-scaled costs would trade away
    soft quality (2% of 16,003 is ~320 soft points)."""
    deadline = time.monotonic() + time_limit
    displaced = displaced_sessions(data, baseline, blockers)
    scope = ripple_scope(data, displaced)
    tiers = [("ripple", {sid: v for sid, v in baseline.items() if sid not in scope})] if scope else []
    tiers.append(("full", None))
    status = None
    for name, freeze in tiers:
        model = TimetableModel(data, blockers=blockers, baseline=baseline, freeze=freeze)
        if model.problems:
            if freeze is None:
                return {"ok": False, "status": "INFEASIBLE", "conflicts": model.problems}
            continue
        left = deadline - time.monotonic()
        if left <= 0.5 and status is not None:
            break
        res = model.solve(time_limit=max(left, 1.0), light_presolve=True)
        status = model.status
        if res is not None:
            res.update(ok=True, scope=name, displaced=sorted(displaced), free_sessions=len(scope) if freeze else len(model.sessions))
            return res
        if status != "INFEASIBLE":
            break           # out of time: no point starting a bigger model
    if status == "INFEASIBLE":
        return {"ok": False, "status": status, "conflicts": diagnose(data, blockers)}
    return {"ok": False, "status": status or "UNKNOWN",
            "conflicts": [f"No repair found within the {time_limit:.0f}s solver budget."]}


def diagnose(data, blockers=()):
    model = TimetableModel(data, blockers=blockers, diagnose=True)
    if model.problems:
        return model.problems
    core = model.conflicting_constraints()
    if core is None:
        return ["The solver ran out of time before it could prove which constraints conflict."]
    return core


# ------------------------------------------------------------------------ exams
def solve_exams(data, time_limit=30, previous=None, free=None):
    """`previous` (an earlier exams output) warm-starts the search. With `free` (exam ids)
    it is a repair instead: every other exam is fixed to its previous slot and rooms."""
    ctx = G.V.Ctx(data)
    cal = data["calendar"]["exam_week"]
    slots = [(d, s["id"]) for d in cal["days"] for s in cal["sessions"]]
    rooms = [r for r in data["rooms"] if "exam" in r["allowed_uses"]]
    exams = data.get("exams", [])
    m = cp_model.CpModel()
    A, Z = {}, {}
    for e in exams:
        for k, (d, ses) in enumerate(slots):
            A[(e["id"], k)] = m.NewBoolVar("")
            for r in rooms:
                closed = any((d, p) in ctx.room_un[r["id"]] for p in G.EXAM_SESSION_PERIODS.get(ses, []))
                if closed:
                    continue
                z = m.NewBoolVar("")
                Z[(e["id"], k, r["id"])] = z
                m.AddImplication(z, A[(e["id"], k)])
            seats = [(r["capacity"] // 2) * Z[(e["id"], k, r["id"])] for r in rooms if (e["id"], k, r["id"]) in Z]
            m.Add(sum(seats) >= e["size"] * A[(e["id"], k)])
        m.AddExactlyOne(A[(e["id"], k)] for k in range(len(slots)))
    for k in range(len(slots)):
        for r in rooms:
            zs = [Z[(e["id"], k, r["id"])] for e in exams if (e["id"], k, r["id"]) in Z]
            if len(zs) > 1:
                m.AddAtMostOne(zs)
    by_group = defaultdict(list)
    for e in exams:
        for g in e["groups"]:
            by_group[g].append(e)
    pen = []
    for g, es in by_group.items():
        for k in range(len(slots)):
            m.AddAtMostOne(A[(e["id"], k)] for e in es)
        for d in cal["days"]:
            ks = [k for k, (dd, _) in enumerate(slots) if dd == d]
            ex = m.NewIntVar(0, len(es), "")
            m.Add(ex >= sum(A[(e["id"], k)] for e in es for k in ks) - 1)
            pen.append(ex)
    # 5 points per extra exam in a day (validator), then fewest rooms, then least empty seats
    m.Minimize(50000 * sum(pen) + 100 * sum(Z.values())
               + sum((ctx.rooms[rid]["capacity"] // 2) * z for (eid, k, rid), z in Z.items()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = default_workers()
    solver.parameters.random_seed = 7
    if previous:
        old = {e["exam_id"]: (slots.index((e["day"], e["session"])), set(e["rooms"]))
               for e in previous if (e["day"], e["session"]) in slots}
        fixed = set(old) - set(free) if free is not None else set()
        for (eid, k), a in A.items():
            if eid in fixed:
                m.Add(a == int(k == old[eid][0]))
            elif eid in old:
                m.AddHint(a, k == old[eid][0])
        for (eid, k, rid), z in Z.items():
            if eid in fixed:
                m.Add(z == int(k == old[eid][0] and rid in old[eid][1]))
            elif eid in old:
                m.AddHint(z, k == old[eid][0] and rid in old[eid][1])
        solver.parameters.cp_model_probing_level = 0
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"ok": False, "status": solver.StatusName(st),
                "conflicts": ["Not enough exam rooms/slots to seat every exam at half capacity without group clashes"]}
    out = []
    for e in exams:
        k = next(k for k in range(len(slots)) if solver.Value(A[(e["id"], k)]))
        rs = sorted([rid for (eid, kk, rid), z in Z.items() if eid == e["id"] and kk == k and solver.Value(z)],
                    key=lambda rid: -ctx.rooms[rid]["capacity"])
        out.append({"exam_id": e["id"], "day": slots[k][0], "session": slots[k][1], "rooms": rs})
    return {"ok": True, "exams": out, "status": solver.StatusName(st)}
