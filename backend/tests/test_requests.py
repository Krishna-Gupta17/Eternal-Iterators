"""Dataset upload, dated requests, the 17:00 cutoff, strict room types and the pending queue."""
import glob
import json
import os
from datetime import timedelta

from backend import guardrails as G
from backend import store
from backend.main import WEEKDAY, today_ist
from backend.paths import DATASET, STARTER


def next_date(day, weeks=1):
    """The `day` (MON..FRI) of the week `weeks` after this one, as YYYY-MM-DD."""
    t = today_ist()
    monday = t - timedelta(days=t.weekday()) + timedelta(weeks=weeks)
    return (monday + timedelta(days=WEEKDAY.index(day))).isoformat()


def _csv_files():
    return [("files", (os.path.basename(p), open(p, "rb").read(), "text/csv"))
            for p in sorted(glob.glob(str(STARTER / "data" / "csv" / "*.csv")))]


# ---------------------------------------------------------------- 0. dataset upload
def test_csv_starter_pack_upload_replaces_the_dataset(client, login):
    h = login("admin1")
    r = client.post("/api/admin/dataset", headers=h, files=_csv_files(), data={"dry_run": "true"})
    info = r.json()
    assert r.status_code == 200 and info["ok"] and info["mode"] == "csv"
    assert info["counts"]["sessions"] == 189 and info["counts"]["rooms"] == 27
    assert store.read()["timetable"]                       # dry run leaves the state alone
    r = client.post("/api/admin/dataset", headers=h, files=_csv_files())
    assert r.status_code == 200, r.text
    st = store.read()
    assert st["timetable"] is None and st["pending"] == []
    assert st["data"]["sessions"] == json.load(open(DATASET, encoding="utf-8"))["sessions"]
    assert client.get("/api/meta", headers=h).json()["dataset_id"] == "cit-public-v1"


def test_unified_json_upload_and_bad_files_are_rejected(client, login):
    h = login("admin1")
    data = json.load(open(DATASET, encoding="utf-8"))
    data["rooms"] = [r for r in data["rooms"] if r["type"] != "lab_phy"]       # PH191 labs lose their room
    r = client.post("/api/admin/dataset", headers=h, files=[("files", ("cit.json", json.dumps(data), "application/json"))])
    assert r.status_code == 400 and "PH191" in r.json()["detail"]
    r = client.post("/api/admin/dataset", headers=h, files=[("files", ("rooms.csv", b"id,name\nX,Y\n", "text/csv"))])
    assert r.status_code == 400 and "missing column" in r.json()["detail"]
    r = client.post("/api/admin/dataset", headers=h, files=[("files", ("cit_public.json", open(DATASET, "rb").read(), "application/json"))])
    assert r.status_code == 200 and r.json()["mode"] == "json"
    assert client.post("/api/admin/dataset", headers=login("f19"), files=_csv_files()).status_code == 403


# ---------------------------------------------------------------- 1. dates
def test_dates_resolve_to_weekdays_and_past_dates_are_refused(client, login):
    h = login("club_quiz")
    form = {"title": "Quiz", "attendance": 40, "duration_periods": 2, "room_types": ["classroom"], "needs": [],
            "windows": [{"date": next_date("TUE"), "from_period": 10, "to_period": 11}]}
    prev = client.post("/api/bookings/preview", headers=h, json=form).json()[0]
    assert prev["available"] and prev["placement"]["day"] == "TUE"
    past = (today_ist() - timedelta(days=3)).isoformat()
    r = client.post("/api/bookings/preview", headers=h, json=dict(form, windows=[{"date": past, "from_period": 10, "to_period": 11}]))
    assert r.status_code == 400 and "past" in r.json()["detail"]
    sat = (today_ist() + timedelta(days=(5 - today_ist().weekday()) % 7 + 7)).isoformat()
    r = client.post("/api/bookings/preview", headers=h, json=dict(form, windows=[{"date": sat, "from_period": 10, "to_period": 11}]))
    assert r.status_code == 400 and "Saturday" in r.json()["detail"]


def test_leave_by_date_needs_a_reason_and_lists_affected_classes(client, login):
    h = login("f19")
    body = {"dates": [next_date("MON"), next_date("TUE")], "periods": list(range(1, 9)), "reason": ""}
    assert client.post("/api/requests/unavailability/check", headers=h, json=body).status_code == 422
    r = client.post("/api/requests/unavailability/check", headers=h, json=dict(body, reason="Conference"))
    res = r.json()
    assert r.status_code == 200 and res["change"]["days"] == ["MON", "TUE"] and res["change"]["dates"] == body["dates"]
    tt = store.assign_of(store.read())
    f19 = [s["id"] for s in store.read()["data"]["sessions"] if s["faculty_id"] == "F19" and tt[s["id"]][0] in ("MON", "TUE")]
    assert sorted(a["id"] for a in res["affected"]) == sorted(f19)


# ---------------------------------------------------------------- 3 + 4. room types and the 17:00 cutoff
def test_no_academic_session_ends_after_five_pm(client, login):
    st = store.read()
    sess = {s["id"]: s for s in st["data"]["sessions"]}
    last = G.academic_last_period(st["data"])
    assert last == 8
    assert all(t + sess[sid]["duration"] - 1 <= last for sid, (d, t, r) in store.assign_of(st).items())
    h = login("f19")
    mine = next(s for s in client.get("/api/requests/my-sessions", headers=h).json() if s["kind"] == "lecture")
    r = client.post("/api/requests/check", headers=h, json={"session_id": mine["id"], "date": next_date("FRI"),
                                                            "start_period": 9, "room_id": mine["room_id"]})
    assert not r.json()["available"] and r.json()["violations"][0].startswith("H15")
    r = client.post("/api/requests/session-move", headers=h, json={"session_id": mine["id"], "day": "FRI",
                                                                   "start_period": 9, "room_id": mine["room_id"]})
    assert r.status_code == 409 and "H15" in r.json()["detail"]


def test_room_type_must_match_strictly(client, login):
    h = login("coord_cse")
    lab = next(s for s in client.get("/api/requests/my-sessions", headers=h).json() if s["room_types"] == ["lab_cs"])
    body = {"session_id": lab["id"], "date": next_date("WED"), "start_period": 2}
    r = client.post("/api/requests/check", headers=h, json=dict(body, room_id="PHY-LAB", room_type="lab_phy"))
    assert r.status_code == 409 and "lab_cs" in r.json()["detail"]
    r = client.post("/api/requests/check", headers=h, json=dict(body, room_id="PHY-LAB", room_type="lab_cs"))
    assert r.status_code == 409 and "PHY-LAB is a lab_phy" in r.json()["detail"]
    v = client.post("/api/requests/check", headers=h, json=dict(body, room_id="PHY-LAB")).json()["violations"]
    assert any(x.startswith("H6") for x in v)
    st = store.read()
    rooms = {r["id"]: r for r in st["data"]["rooms"]}
    sess = {s["id"]: s for s in st["data"]["sessions"]}
    for sid, (d, t, rid) in store.assign_of(st).items():
        assert rooms[rid]["type"] in sess[sid]["room_types"] and sess[sid]["kind"] in rooms[rid]["allowed_uses"]


def test_extra_lab_class_only_lists_matching_lab(client, login):
    h = login("coord_cse")
    lab = next(s for s in client.get("/api/requests/my-sessions", headers=h).json() if s["room_types"] == ["lab_cs"])
    extra = {"course_id": lab["course_id"], "kind": "lab", "groups": lab["groups"]}
    for day in ("MON", "TUE", "WED", "THU", "FRI"):
        for start in (2, 6):
            for room in ("CS-LAB-1", "CS-LAB-2"):
                res = client.post("/api/requests/check", headers=h, json={"extra": extra, "date": next_date(day), "start_period": start,
                                                                          "room_id": room, "room_type": "lab_cs"}).json()
                if res["available"]:
                    assert res["session"]["kind"] == "lab" and res["session"]["room_types"] == ["lab_cs"]
                    assert res["session"]["duration"] == lab["duration"]
                    return
    raise AssertionError("no free CS lab slot for an extra lab")


# ---------------------------------------------------------------- 5. check gate + pending queue
FREE = {"title": "Quiz night", "attendance": 40, "duration_periods": 2, "room_types": ["classroom"], "needs": [],
        "requested_room": "CR-105"}


def test_two_pending_requests_for_one_free_slot_then_approval_rejects_the_other(client, login):
    form = dict(FREE, windows=[{"date": next_date("THU"), "from_period": 11, "to_period": 12}])
    qa, rb = login("club_quiz"), login("club_robotics")
    pa = client.post("/api/bookings/preview", headers=qa, json=form).json()[0]
    assert pa["available"] and pa["queued"] and pa["pending_queue"] == []
    a = client.post("/api/bookings", headers=qa, json=form).json()[0]
    assert a["status"] == "pending" and a["placement"]["room_id"] == "CR-105"
    pb = client.post("/api/bookings/preview", headers=rb, json=form).json()[0]
    assert pb["available"] and pb["pending_queue"] == [a["id"]]          # pending doesn't block
    b = client.post("/api/bookings", headers=rb, json=form).json()[0]
    assert b["status"] == "pending" and a["id"] in b["reason"]
    r = client.post(f"/api/bookings/{b['id']}/approve", headers=login("admin1"))
    assert r.status_code == 200 and r.json()["rejected_competitors"] == [a["id"]]
    a2 = next(x for x in client.get("/api/bookings", headers=qa).json() if x["id"] == a["id"])
    assert a2["status"] == "rejected" and b["id"] in a2["reason"]
    pc = client.post("/api/bookings/preview", headers=qa, json=form).json()[0]
    assert pc["placement"] is None or pc["placement"]["room_id"] != "CR-105" or not pc["available"]


def test_slot_held_by_a_class_cannot_be_submitted(client, login):
    st = store.read()
    sess = {s["id"]: s for s in st["data"]["sessions"]}
    sid, (d, t, rid) = next((k, v) for k, v in store.assign_of(st).items()
                            if sess[k]["kind"] == "lecture" and v[2].startswith("CR"))
    form = dict(FREE, requested_room=rid, room_types=["classroom"], duration_periods=1,
                windows=[{"date": next_date(d), "from_period": t, "to_period": t}], fallbacks=[])
    h = login("club_quiz")
    prev = client.post("/api/bookings/preview", headers=h, json=form).json()[0]
    if prev["available"]:                                     # another classroom was free at that hour
        assert prev["placement"]["room_id"] != rid
        return
    assert prev["unavailable_reason"]
    r = client.post("/api/bookings", headers=h, json=form)
    assert r.status_code == 409 and r.json()["detail"].startswith("Unavailable")


def test_competing_session_moves_queue_and_the_loser_is_superseded(client, login):
    h = login("f19")
    mine = [s for s in client.get("/api/requests/my-sessions", headers=h).json() if s["kind"] == "lecture"]
    target = None
    for d in ("MON", "TUE", "WED", "THU", "FRI"):
        for t in range(1, 9):
            for room in ("LH-1", "LH-2", "AUD"):
                a = client.post("/api/requests/check", headers=h, json={"session_id": mine[0]["id"], "date": next_date(d),
                                                                        "start_period": t, "room_id": room}).json()
                b = client.post("/api/requests/check", headers=h, json={"session_id": mine[1]["id"], "date": next_date(d),
                                                                        "start_period": t, "room_id": room}).json()
                if a["available"] and b["available"]:
                    target = (d, t, room)
                    break
            if target:
                break
        if target:
            break
    assert target, "no slot both F19 lectures could use"
    d, t, room = target
    ids = []
    for s in mine[:2]:
        r = client.post("/api/requests/session-move", headers=h, json={"session_id": s["id"], "date": next_date(d),
                                                                       "start_period": t, "room_id": room})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    assert "queue_note" in next(c for c in client.get("/api/changes", headers=h).json() if c["id"] == ids[1])
    assert client.post(f"/api/changes/{ids[0]}/approve", headers=login("admin1")).status_code == 200
    loser = next(c for c in client.get("/api/changes", headers=h).json() if c["id"] == ids[1])
    assert loser["status"] == "rejected" and "Superseded" in loser["note"]
