"""Deterministic validity checks. Nothing in here calls an LLM.

Two layers:
  * `official_report` runs the organisers' validator.py unchanged (loaded from the
    starter pack), so the scoreboard and every state-changing commit are judged by
    exactly the same code the judges run.
  * `Occupancy` is a fast incremental checker that mirrors the same rules for one
    candidate placement at a time (used when searching for free slots/rooms).
"""
import importlib.util
import os
from collections import defaultdict

from .paths import VALIDATOR

_spec = importlib.util.spec_from_file_location("cit_validator", VALIDATOR)
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

LAST_TEACHING = V.LAST_TEACHING
MAX_PERIOD = V.MAX_PERIOD
CAT_WEIGHT = V.CAT_WEIGHT
EXAM_SESSION_PERIODS = {"AM": [2, 3, 4], "PM": [6, 7]}
# Academic classes (regular, rescheduled and extra) must end by this time; later periods are
# reserved for events. Stricter than validator.py, which allows teaching up to P9 (18:00).
ACADEMIC_CUTOFF = os.getenv("ACADEMIC_CUTOFF", "17:00")
SESSION_KINDS = ("lecture", "tutorial", "lab")
LAB_WINDOW = 3          # a lab window runs from each lab start period: P2-P4 and P6-P8 on the CIT calendar
LAB_LAST_PERIOD = 9


def official_report(data, schedule, baseline=None):
    return V.run(data, schedule, baseline)


def effective_category(data, req):
    return V.effective_category(data, req)


def academic_last_period(data):
    """Last teaching period that ends by ACADEMIC_CUTOFF (P8 on the CIT calendar)."""
    ok = [p["id"] for p in data["calendar"]["periods"]
          if p.get("teaching", p["id"] <= LAST_TEACHING) and p["end"] <= ACADEMIC_CUTOFF]
    return min(max(ok, default=LAST_TEACHING), LAST_TEACHING)


def valid_starts(ctx, s):
    """Start periods allowed by H9/H14 for a session (lunch, lab starts, fixed slot, 17:00 cutoff)."""
    dur = s["duration"]
    out = []
    for t in range(1, academic_last_period(ctx.data) - dur + 2):
        per = V.block(t, dur)
        if per[0] <= ctx.lunch < per[-1]:
            continue
        if s["kind"] == "lab" and t not in ctx.lab_starts:
            continue
        out.append(t)
    fx = s.get("fixed_slot")
    if fx:
        out = [t for t in out if t == fx["start_period"]]
    return out


def valid_days(ctx, s):
    fx = s.get("fixed_slot")
    return [fx["day"]] if fx else list(ctx.days)


def room_allows_kind(room, kind):
    """A room may only host the session kinds in its allowed_uses (no lectures in seminar halls)."""
    return kind in room.get("allowed_uses", SESSION_KINDS)


def is_lab_room(room):
    return "lab" in room.get("allowed_uses", ())


def lab_windows(data):
    """Contiguous period blocks a lab may occupy; a lab never spans two of them (or lunch)."""
    return [list(range(s, s + LAB_WINDOW)) for s in data["calendar"].get("lab_start_periods", [2, 6])]


def class_request_problems(data, s, start, room):
    """Why a class request is malformed on its own, before any clash check: the room's type must
    be one the session needs (Rule A), and a lab, or any class in a lab room, must sit inside one
    lab window (Rule B). Such requests are refused at submission and never reach the Admin queue."""
    out = []
    if room["type"] not in s["room_types"]:
        out.append(f"H6: {room['id']} is a {room['type']}; this {s['kind']} needs a {' / '.join(s['room_types'])} room")
    elif not room_allows_kind(room, s["kind"]):
        out.append(f"H6: {room['id']} ({room['type']}) does not take {s['kind']}s")
    if s["kind"] == "lab" or is_lab_room(room):
        per = V.block(start, s["duration"])
        wins = lab_windows(data)
        if not any(set(per) <= set(w) for w in wins):
            out.append(f"H9: labs run only in {' or '.join(f'P{w[0]}-P{w[-1]}' for w in wins)}; "
                       f"P{per[0]}-P{per[-1]} falls outside them")
    return out


def room_fits_session(room, s):
    return (room["type"] in s["room_types"] and room_allows_kind(room, s["kind"]) and room["capacity"] >= s["size"]
            and (room["accessible"] or not s.get("needs_accessible")))


def room_fits_request(room, req):
    feats = set(room["features"]) | ({"accessible"} if room["accessible"] else set())
    return ("event" in room["allowed_uses"] and room["type"] in req["room_types"]
            and room["capacity"] >= req["attendance"] and not (set(req["needs"]) - feats))


def is_lab_request(req):
    return any(room_type.startswith("lab_") for room_type in req.get("room_types", []))


def request_fit_problems(room, req):
    """Human-readable reasons a room can never host a request (ignoring time)."""
    out = []
    feats = set(room["features"]) | ({"accessible"} if room["accessible"] else set())
    if "event" not in room["allowed_uses"]:
        out.append(f"{room['id']} cannot host events")
    if room["type"] not in req["room_types"]:
        out.append(f"{room['id']} is a {room['type']}, not one of {', '.join(req['room_types'])}")
    if is_lab_request(req) and room["type"].startswith("lab_") is False:
        out.append(f"{room['id']} is not a compatible laboratory for this request")
    if room["capacity"] < req["attendance"]:
        out.append(f"{room['id']} seats {room['capacity']}, request needs {req['attendance']}")
    miss = set(req["needs"]) - feats
    if miss:
        out.append(f"{room['id']} lacks {', '.join(sorted(miss))}")
    return out


class Occupancy:
    """Who uses what at each (day, period). Built from a week view: base timetable plus
    Week-7 overrides plus placed bookings. `ignore` removes entities (the thing being
    moved) so a candidate can be checked against everything else."""

    def __init__(self, data, assign, placed_bookings=(), ignore=()):
        self.data = data
        self.ctx = V.Ctx(data)
        ignore = set(ignore)
        self.room = defaultdict(list)
        self.fac = defaultdict(list)
        self.grp = defaultdict(set)
        self.fac_hours = defaultdict(int)
        self.basket_time = {}
        self.mandatory = defaultdict(list)   # (g, day, p) -> booking ids
        for sid, (day, start, rid) in assign.items():
            s = self.ctx.sessions.get(sid)
            if s is None or sid in ignore:
                continue
            self._add_session(s, day, start, rid)
        for b in placed_bookings:
            if b["request_id"] in ignore:
                continue
            for p in range(b["start_period"], b["end_period"] + 1):
                self.room[(b["room_id"], b["day"], p)].append(b["request_id"])
                for g in b.get("mandatory_for_groups", []):
                    self.mandatory[(g, b["day"], p)].append(b["request_id"])

    def _add_session(self, s, day, start, rid):
        for p in V.block(start, s["duration"]):
            self.room[(rid, day, p)].append(s["id"])
            self.fac[(s["faculty_id"], day, p)].append(s["id"])
            for g in s["groups"]:
                self.grp[(g, day, p)].add(s.get("basket_slot") or s["id"])
        self.fac_hours[(s["faculty_id"], day)] += s["duration"]
        if s.get("basket_slot"):
            self.basket_time[s["basket_slot"]] = (day, start)

    def session_violations(self, s, day, start, rid, check_basket=True):
        """Rule-coded reasons why session `s` cannot sit at (day, start, rid)."""
        ctx, out = self.ctx, []
        if day not in ctx.days:
            return [f"H1: invalid day {day}"]
        dur = s["duration"]
        if start < 1 or start + dur - 1 > LAST_TEACHING:
            return [f"H9: P{start} with duration {dur} runs outside teaching periods P1-P9"]
        last = academic_last_period(self.data)
        if start + dur - 1 > last:
            return [f"H15: P{start}-P{start + dur - 1} ends after {ACADEMIC_CUTOFF}; academic classes must finish by "
                    f"P{last} and later periods are reserved for events"]
        per = V.block(start, dur)
        if per[0] <= ctx.lunch < per[-1]:
            out.append(f"H9: P{per[0]}-P{per[-1]} crosses the lunch break")
        if s["kind"] == "lab" and start not in ctx.lab_starts:
            out.append(f"H9: a 3-hour lab must start at P2 or P6, not P{start}")
        fx = s.get("fixed_slot")
        if fx and (fx["day"] != day or fx["start_period"] != start):
            out.append(f"H14: fixed at {fx['day']} P{fx['start_period']}")
        r = ctx.rooms.get(rid)
        if r is None:
            return out + [f"H1: unknown room {rid}"]
        if r["capacity"] < s["size"]:
            out.append(f"H5: {rid} seats {r['capacity']}, session has {s['size']}")
        if r["type"] not in s["room_types"]:
            out.append(f"H6: {rid} is a {r['type']}, session needs {', '.join(s['room_types'])}")
        elif not room_allows_kind(r, s["kind"]):
            out.append(f"H6: {rid} ({r['type']}) does not take {s['kind']}s")
        if s.get("needs_accessible") and not r["accessible"]:
            out.append(f"H10: {rid} is not wheelchair accessible")
        f = s["faculty_id"]
        for p in per:
            if (day, p) in ctx.fac_un[f]:
                out.append(f"H7: {f} is unavailable {day} P{p}")
            if (day, p) in ctx.room_un[rid]:
                out.append(f"H8: {rid} is closed {day} P{p}")
            if self.room[(rid, day, p)]:
                out.append(f"H4: {rid} is in use {day} P{p} by {', '.join(self.room[(rid, day, p)])}")
            if self.fac[(f, day, p)]:
                out.append(f"H2: {f} already teaches {', '.join(self.fac[(f, day, p)])} {day} P{p}")
            key = s.get("basket_slot") or s["id"]
            for g in s["groups"]:
                other = self.grp[(g, day, p)] - {key}
                if other:
                    out.append(f"H3: group {g} already has {', '.join(sorted(other))} {day} P{p}")
                if self.mandatory[(g, day, p)]:
                    out.append(f"H13: group {g} must attend {', '.join(self.mandatory[(g, day, p)])} {day} P{p}")
        mx = ctx.fac.get(f, {}).get("max_hours_per_day", 4)
        if self.fac_hours[(f, day)] + dur > mx:
            out.append(f"H12: {f} would teach {self.fac_hours[(f, day)] + dur}h on {day} (limit {mx}h)")
        b = s.get("basket_slot")
        if check_basket and b and b in self.basket_time and self.basket_time[b] != (day, start):
            d0, t0 = self.basket_time[b]
            out.append(f"H11: basket {b} runs at {d0} P{t0}; all its sessions must move together")
        return out

    def booking_violations(self, req, rid, day, a, b):
        ctx, out = self.ctx, []
        room = ctx.rooms.get(rid)
        if room is None:
            return [f"unknown room {rid}"]
        if day not in ctx.days or a < 1 or b > MAX_PERIOD or b < a:
            return [f"invalid time {day} P{a}-P{b}"]
        if b - a + 1 != req["duration_periods"]:
            out.append(f"booked {b - a + 1} periods, request needs {req['duration_periods']}")
        if is_lab_request(req) and b > LAB_LAST_PERIOD:
            out.append(f"laboratory bookings are limited to P1-P{LAB_LAST_PERIOD}")
        out += request_fit_problems(room, req)
        for p in range(a, b + 1):
            if (day, p) in ctx.room_un[rid]:
                out.append(f"{rid} is closed for maintenance {day} P{p}")
            if self.room[(rid, day, p)]:
                out.append(f"{rid} {day} P{p} is already used by {', '.join(self.room[(rid, day, p)])}")
            for g in req.get("mandatory_for_groups", []):
                if self.grp[(g, day, p)]:
                    out.append(f"mandatory for {g}, who have {', '.join(sorted(self.grp[(g, day, p)]))} {day} P{p}")
        return out

    def free_rooms(self, day, periods, min_capacity=0, room_type=None, accessible=False):
        out = []
        for r in self.data["rooms"]:
            if r["capacity"] < min_capacity or (room_type and r["type"] != room_type):
                continue
            if accessible and not r["accessible"]:
                continue
            if any(self.room[(r["id"], day, p)] or (day, p) in self.ctx.room_un[r["id"]] for p in periods):
                continue
            out.append(r)
        return sorted(out, key=lambda r: r["capacity"])
