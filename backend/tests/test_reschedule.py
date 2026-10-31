import json

from backend import guardrails as G
from backend import scheduling as S
from backend import store
from backend.paths import SAMPLE_DISRUPTION


def test_sample_disruption_min_moves_and_approval_gate():
    changes = json.load(open(SAMPLE_DISRUPTION, encoding="utf-8"))["changes"]
    with store.transaction() as st:
        base_sched = store.export_schedule(st)
        item = S.propose(st, changes, "admin1", "test")
    assert item["status"] == "pending"
    assert item["score_after"]["hard"] == 0
    assert 0 < item["moved_count"] <= 25
    assert store.read()["timetable"] == {k: list(v) for k, v in store.assign_of(store.read()).items()}
    # nothing applied before approval
    assert store.read()["changes"] == []
    with store.transaction() as st:
        S.approve(st, item["id"], "admin2")
    st = store.read()
    data = G.V.apply_disruption(st["original_data"], {"changes": changes})
    rep = G.official_report(data, store.export_schedule(st), base_sched)
    assert rep["hard_violation_count"] == 0
    assert rep["stability"]["sessions_moved"] == item["moved_count"]


def test_infeasible_change_names_the_conflict():
    ch = [{"type": "faculty_unavailable", "faculty_id": "F03", "days": ["MON", "TUE", "WED", "THU"],
           "periods": list(range(1, 10)), "reason": "long leave"}]
    with store.transaction() as st:
        item = S.propose(st, ch, "admin1", "test")
    assert item["status"] == "infeasible"
    text = " ".join(item["conflicts"])
    assert "F03" in text and "long leave" in text
