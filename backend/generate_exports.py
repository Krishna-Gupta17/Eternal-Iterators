"""Produce exports/schedule.json and exports/schedule_after.json by driving the app's own API
(the same endpoints the Admin buttons call), then run the official validator on both.

    python -m backend.generate_exports
"""
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from .main import app
from .paths import DATASET, EXPORTS, SAMPLE_DISRUPTION, VALIDATOR
from .rbac import DEMO_PASSWORD


def wait(c, h):
    while True:
        j = c.get("/api/jobs/current", headers=h).json()
        if j["status"] != "running":
            if j["status"] == "failed":
                sys.exit(f"Job failed: {j['error']}")
            return j
        time.sleep(2)


def main():
    c = TestClient(app)
    h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin1", "password": DEMO_PASSWORD}).json()["token"]}
    c.post("/api/admin/reset", headers=h).raise_for_status()
    print("Generating timetable, exams and Spectra decisions ...")
    c.post("/api/admin/generate", headers=h).raise_for_status()
    print(" ", wait(c, h)["result"])
    print(" ", c.get("/api/export/schedule", headers=h).json()["written"])

    print("Applying sample_disruption.json ...")
    with open(SAMPLE_DISRUPTION, "rb") as f:
        c.post("/api/changes/upload", headers=h, files={"file": ("sample_disruption.json", f, "application/json")}).raise_for_status()
    print(" ", wait(c, h)["result"])
    change = c.get("/api/changes", headers=h).json()[0]
    if change["status"] != "pending":
        sys.exit(f"No valid fix: {change.get('conflicts')}")
    print(f"  proposal {change['id']}: {change['moved_count']} sessions moved; approving as admin1")
    c.post(f"/api/changes/{change['id']}/approve", headers=h).raise_for_status()
    print(" ", c.get("/api/export/schedule", headers=h).json()["written"])

    for args in (["--schedule", str(EXPORTS / "schedule.json")],
                 ["--schedule", str(EXPORTS / "schedule_after.json"), "--disruption", str(SAMPLE_DISRUPTION),
                  "--baseline", str(EXPORTS / "schedule.json")]):
        print("\n$ validator.py", " ".join(a.split("\\")[-1].split("/")[-1] for a in args))
        subprocess.run([sys.executable, str(VALIDATOR), "--data", str(DATASET), "--quiet", *args], check=True)


if __name__ == "__main__":
    main()
