import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend import ai_service, scheduling, store
from backend.database import Base
from backend.main import app
from backend.rbac import DEMO_PASSWORD

_generated = {}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test gets its own isolated SQLite database and starts from a generated timetable.

    Instead of patching STATE_FILE (old file-based approach), we patch
    ``store._session_factory`` to a factory bound to a fresh, per-test SQLite
    database.  The ``store._state`` cache is reset so the first DB read triggers
    a reload from the new empty DB.
    """
    # ── create a fresh isolated SQLite DB for this test ──
    test_engine = create_engine(
        f"sqlite:///{tmp_path}/test_state.db",
        connect_args={"check_same_thread": False},
    )
    with test_engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    Base.metadata.create_all(test_engine)
    test_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # ── patch store so all DB operations use the test DB ──
    monkeypatch.setattr(store, "_state", None)
    monkeypatch.setattr(store, "_session_factory", test_factory)
    monkeypatch.setattr(ai_service, "API_KEY", "")  # tests never hit a real LLM

    # ── seed with a pre-generated timetable (generated once, reused across tests) ──
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
