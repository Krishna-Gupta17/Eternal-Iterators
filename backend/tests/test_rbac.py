LAB_FORM = {"title": "CSE lab demo", "attendance": 40, "duration_periods": 2,
            "windows": [{"day": "MON", "from_period": 10, "to_period": 11}],
            "room_types": ["lab_cs"], "needs": ["computers"], "requested_room": "CS-LAB-1"}


def _submit(client, h, form=LAB_FORM):
    r = client.post("/api/bookings", json=form, headers=h)
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_coordinator_cannot_approve_own_lab_booking(client, login):
    cse = login("coord_cse")
    rec = _submit(client, cse)
    assert rec["status"] == "pending"
    assert client.post(f"/api/bookings/{rec['id']}/approve", headers=cse).status_code == 403
    # another department's coordinator cannot approve a CSE lab either
    assert client.post(f"/api/bookings/{rec['id']}/approve", headers=login("coord_ece")).status_code == 403
    r = client.post(f"/api/bookings/{rec['id']}/approve", headers=login("admin1"))
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_admin_cannot_approve_own_request_but_second_admin_can(client, login):
    a1 = login("admin1")
    rec = _submit(client, a1)
    r = client.post(f"/api/bookings/{rec['id']}/approve", headers=a1)
    assert r.status_code == 403 and "own request" in r.json()["detail"]
    assert client.post(f"/api/bookings/{rec['id']}/approve", headers=login("admin2")).status_code == 200


def test_department_coordinator_approves_their_lab(client, login):
    rec = _submit(client, login("club_quiz"))
    assert rec["status"] == "pending"
    assert client.post(f"/api/bookings/{rec['id']}/approve", headers=login("coord_cse")).status_code == 200


def test_club_and_faculty_cannot_approve(client, login):
    rec = _submit(client, login("coord_cse"))
    for u in ("club_robotics", "f19"):
        assert client.post(f"/api/bookings/{rec['id']}/approve", headers=login(u)).status_code == 403


def test_faculty_move_request_needs_another_user(client, login):
    f19 = login("f19")
    res = client.post("/api/assistant/faculty", headers=f19,
                      json={"text": "Move my MA101-AB-L1 lecture to Thursday", "today": "MON"}).json()
    assert res["intent"]["intent"] == "MOVE_SESSION"
    if not res["options"]:
        res = client.post("/api/assistant/faculty", headers=f19, json={"text": "Move MA101-AB-L1", "today": "MON"}).json()
    opt = res["options"][0]
    r = client.post("/api/requests/session-move", headers=f19,
                    json={"session_id": res["session"]["id"], **{k: opt[k] for k in ("day", "start_period", "room_id")}})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert client.post(f"/api/changes/{cid}/approve", headers=f19).status_code == 403
    r = client.post(f"/api/changes/{cid}/approve", headers=login("admin1"))
    assert r.status_code == 200 and r.json()["moved_count"] == 1


def test_clashing_manual_move_is_blocked(client, login):
    f19 = login("f19")
    tt = client.get("/api/timetable", headers=f19).json()["sessions"]
    mine = next(s for s in tt if s["faculty_id"] == "F19")
    other = next(s for s in tt if s["id"] != mine["id"] and set(s["groups"]) & set(mine["groups"]))
    r = client.post("/api/requests/session-move", headers=f19,
                    json={"session_id": mine["id"], "day": other["day"], "start_period": other["start_period"],
                          "room_id": other["room_id"]})
    assert r.status_code == 409
    assert "H3" in r.json()["detail"] or "H4" in r.json()["detail"]
