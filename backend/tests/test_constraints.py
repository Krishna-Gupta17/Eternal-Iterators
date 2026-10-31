from backend import guardrails as G
from backend import scheduling as S
from backend import store


def _st():
    return store.read()


def test_generated_timetable_passes_official_validator():
    rep = S.report(_st())
    assert rep["hard_violation_count"] == 0, rep["hard_violations"][:5]
    assert rep["bookings"]["priority_inversions"] == []
    assert rep["exam_soft_penalty"] == 0


def test_exam_half_capacity_enforced():
    st = _st()
    rooms = {r["id"]: r for r in st["data"]["rooms"]}
    size = {e["id"]: e["size"] for e in st["data"]["exams"]}
    for e in st["exams"]:
        assert sum(rooms[r]["capacity"] // 2 for r in e["rooms"]) >= size[e["exam_id"]]
    # the validator itself treats a 90-seat room as 45 seats during exams
    sched = store.export_schedule(st)
    sched["exams"] = [dict(e, rooms=["CR-101"]) if e["exam_id"] == "EX-CS201" else e for e in sched["exams"]]
    clash_free = [e for e in sched["exams"] if e["exam_id"] == "EX-CS201" or "CR-101" not in e["rooms"]]
    sched["exams"] = clash_free + [e for e in sched["exams"] if e not in clash_free]
    rep = G.official_report(st["data"], sched)
    assert any(v["rule"] == "H5" and "EX-CS201" in v["detail"] and "45 seats" in v["detail"] for v in rep["hard_violations"])


def test_three_hour_labs_only_start_at_p2_or_p6():
    st = _st()
    ctx = G.V.Ctx(st["data"])
    lab = next(s for s in st["data"]["sessions"] if s["kind"] == "lab")
    assert G.valid_starts(ctx, lab) == [2, 6]
    occ = G.Occupancy(st["data"], {}, ())
    room = next(r for r in st["data"]["rooms"] if G.room_fits_session(r, lab))
    assert any(v.startswith("H9") for v in occ.session_violations(lab, "MON", 4, room["id"]))
    assert any("lunch" in v for v in occ.session_violations(dict(lab, kind="lecture"), "MON", 4, room["id"]))
    for sid, (d, t, r) in store.assign_of(st).items():
        if ctx.sessions[sid]["kind"] == "lab":
            assert t in (2, 6)


def test_elective_basket_synchronised_and_not_clashing():
    st = _st()
    assign = store.assign_of(st)
    baskets = {}
    for s in st["data"]["sessions"]:
        if s.get("basket_slot"):
            baskets.setdefault(s["basket_slot"], []).append(s["id"])
    for b, sids in baskets.items():
        assert len({assign[x][:2] for x in sids}) == 1, b
        assert len({assign[x][2] for x in sids}) == len(sids), b
    a, b2 = baskets["OEB-1"][:2]
    s_a = next(s for s in st["data"]["sessions"] if s["id"] == a)
    occ = G.Occupancy(st["data"], assign, (), ignore=[a])
    d, t, _ = assign[a]
    room = next(r["id"] for r in occ.free_rooms(d, [t], s_a["size"]) if G.room_fits_session(r, s_a))
    # same time as its basket peers: no group clash with them
    assert not [v for v in occ.session_violations(s_a, d, t, room) if v.startswith(("H3", "H11"))]
    # moving one elective alone breaks synchronisation
    other_day = next(x for x in ("MON", "TUE", "WED", "THU", "FRI") if x != d)
    assert any(v.startswith("H11") for v in occ.session_violations(s_a, other_day, t, room))


def test_week7_bookings_never_overlap_classes_or_mandatory_groups():
    st = _st()
    occ = G.Occupancy(st["data"], store.week_assign(st), ())
    for b in store.placed_bookings(st):
        req = next(r["request"] for r in st["requests"] if r["id"] == b["request_id"])
        assert not occ.booking_violations(req, b["room_id"], b["day"], b["start_period"], b["end_period"])
