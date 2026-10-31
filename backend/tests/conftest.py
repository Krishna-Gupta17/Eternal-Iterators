import copy

import pytest
from fastapi.testclient import TestClient

from backend import ai_service, scheduling, store
from backend.main import app
from backend.rbac import DEMO_PASSWORD

_generated = {}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test gets its own state file and starts from a generated timetable."""
    monkeypatch.setattr(store, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(store, "_state", None)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(ai_service, "API_KEY", "")          # tests never hit a real LLM
    if "st" not in _generated:
        st = store.fresh_state()
        scheduling.generate(st, "pytest", time_limit=60)
        _generated["st"] = st
    store.reset()
    with store.transaction() as w:
        w.clear()
        w.update(copy.deepcopy(_generated["st"]))
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def login(client):
    def _login(username):
        r = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["token"]}
    return _login
