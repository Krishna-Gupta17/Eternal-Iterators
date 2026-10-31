"""Demo accounts, JWT auth and permission checks."""
import hashlib
import os
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET = os.getenv("JWT_SECRET") or "dev-only-secret-change-me-in-production-0123456789"
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo123")

ADMIN, COORD, FACULTY, CLUB = "admin", "department_coordinator", "faculty", "club"
ROLE_LABEL = {ADMIN: "Admin (Dean's office)", COORD: "Department coordinator", FACULTY: "Faculty", CLUB: "Club representative"}

USERS = {
    "admin1": {"name": "Dean's Office (Admin 1)", "role": ADMIN},
    "admin2": {"name": "Dean's Office (Admin 2)", "role": ADMIN},
    "coord_cse": {"name": "CSE Coordinator", "role": COORD, "department": "CSE"},
    "coord_ece": {"name": "ECE Coordinator", "role": COORD, "department": "ECE"},
    "coord_ce": {"name": "Civil Coordinator", "role": COORD, "department": "CE"},
    "f03": {"name": "Faculty F03 (CSE)", "role": FACULTY, "faculty_id": "F03", "department": "CSE"},
    "f12": {"name": "Dr. Amit Behera (F12, ECE)", "role": FACULTY, "faculty_id": "F12", "department": "ECE"},
    "f19": {"name": "Faculty F19 (Maths)", "role": FACULTY, "faculty_id": "F19", "department": "MA"},
    "club_robotics": {"name": "Robotics Club", "role": CLUB, "club": "Robotics Club"},
    "club_quiz": {"name": "Quiz Club", "role": CLUB, "club": "Quiz Club"},
}
_HASH = hashlib.sha256(DEMO_PASSWORD.encode()).hexdigest()


def public_user(username):
    u = USERS[username]
    return dict(u, username=username, role_label=ROLE_LABEL[u["role"]])


def login(username, password):
    if username not in USERS or hashlib.sha256(password.encode()).hexdigest() != _HASH:
        raise HTTPException(401, "Wrong username or password")
    token = jwt.encode({"sub": username, "exp": int(time.time()) + 12 * 3600}, SECRET, algorithm="HS256")
    return {"token": token, "user": public_user(username)}


_bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials = Depends(_bearer)):
    if cred is None:
        raise HTTPException(401, "Log in first")
    try:
        sub = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, log in again")
    if sub not in USERS:
        raise HTTPException(401, "Unknown user")
    return public_user(sub)


def require(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, f"{ROLE_LABEL[user['role']]} cannot do this")
        return user
    return dep


def forbid_self_approval(user, requester):
    if requester and requester == user["username"]:
        raise HTTPException(403, "Nobody can approve their own request. Another authorised user must approve it.")


def can_approve_booking(user, rule):
    """rule from bookings.approver_rule: 'admin' or 'coordinator:CSE/...'."""
    if user["role"] == ADMIN:
        return True
    if user["role"] == COORD and rule.startswith("coordinator:"):
        return user.get("department") in rule.split(":", 1)[1].split("/")
    return False
