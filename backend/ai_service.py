"""LLM layer: reads requests, fills forms, phrases explanations. Never decides validity.

If LLM_API_KEY is missing or the call fails / times out / returns bad JSON, every
function falls back to the deterministic parser in nlp_fallback.py.
"""
import json
import os
import re
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import guardrails as G
from . import nlp_fallback as NLP
from .paths import PROMPTS

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
API_KEY = os.getenv("LLM_API_KEY", "")
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "groq": "openai/gpt-oss-120b",
}
MODEL = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(PROVIDER, "")
TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

DayT = Literal["MON", "TUE", "WED", "THU", "FRI"]
ROOM_TYPES = {"classroom", "lecture_hall", "auditorium", "seminar_hall", "outdoor", "lab_cs", "lab_elec", "lab_phy", "workshop", "lab_civil"}
NEEDS = {"projector", "pa_system", "stage", "computers", "accessible", "power_benches", "ac"}


class Window(BaseModel):
    day: DayT
    from_period: int = Field(ge=1, le=16)
    to_period: int = Field(ge=1, le=16)


class Pref(BaseModel):
    windows: list[Window] = []
    requested_room: Optional[str] = None


class BookingDraft(BaseModel):
    title: str = "Venue booking"
    attendance: Optional[int] = Field(default=None, ge=1, le=5000)
    duration_periods: Optional[int] = Field(default=None, ge=1, le=13)
    windows: list[Window] = []
    room_types: list[str] = []
    needs: list[str] = []
    requested_room: Optional[str] = None
    mandatory_for_groups: list[str] = []
    fallbacks: list[Pref] = []
    series: bool = False
    claims: list[str] = []
    assumptions: list[str] = []
    follow_up: Optional[str] = None


class FacultyIntent(BaseModel):
    intent: Literal["MOVE_SESSION", "ADD_EXTRA_SESSION", "MARK_UNAVAILABILITY", "UNKNOWN"]
    session_id: Optional[str] = None
    course_id: Optional[str] = None
    days: list[DayT] = []
    periods: Optional[list[int]] = None
    target_days: Optional[list[DayT]] = None
    target_periods: Optional[list[int]] = None
    duration: int = Field(default=1, ge=1, le=3)
    reason: str = ""


class LLMUnavailable(Exception):
    pass


def status():
    return {"provider": PROVIDER if API_KEY else "none", "model": MODEL if API_KEY else None,
            "configured": bool(API_KEY)}


def _prompt(name, **kw):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def _call(system, user):
    if not API_KEY or PROVIDER == "none":
        raise LLMUnavailable("No LLM API key configured")
    try:
        if PROVIDER == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=TIMEOUT,
                           headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
                           json={"model": MODEL, "max_tokens": 1200, "system": system,
                                 "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
        if PROVIDER == "openai":
            r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=TIMEOUT,
                           headers={"Authorization": f"Bearer {API_KEY}"},
                           json={"model": MODEL, "messages": [{"role": "system", "content": system},
                                                              {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        if PROVIDER == "gemini":
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                           params={"key": API_KEY}, timeout=TIMEOUT,
                           json={"systemInstruction": {"parts": [{"text": system}]},
                                 "contents": [{"role": "user", "parts": [{"text": user}]}]})
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        if PROVIDER == "groq":
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                timeout=TIMEOUT,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_completion_tokens": 1200,
                },
            )
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMUnavailable(f"{PROVIDER} call failed: {type(e).__name__}: {str(e)[:160]}")
    raise LLMUnavailable(f"Unknown LLM_PROVIDER {PROVIDER}")


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMUnavailable("LLM did not return JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"LLM returned invalid JSON: {e}")


# ---------------------------------------------------------------- booking parser
def parse_booking(text, data, today, user):
    """Returns {draft, source, llm_error, warnings, security}. `draft` is only a form pre-fill."""
    fb = NLP.parse_booking(text, data, today=today, role=user["role"])
    flags, claims = NLP.security_flags(text)
    source, err, draft = "fallback", None, None
    try:
        system = _prompt("booking_parser.md", today=today,
                         rooms=", ".join(f"{r['id']} ({r['type']}, {r['capacity']})" for r in data["rooms"]),
                         groups=", ".join(g["id"] for g in data["student_groups"]))
        raw = _json(_call(system, text))
        d = BookingDraft.model_validate(raw)
        draft = d.model_dump()
        draft["claimed_categories"] = claims
        draft["security_flags"] = flags
        source = "llm"
    except (LLMUnavailable, ValidationError) as e:
        err = str(e)[:300]
    if draft is None:
        draft = fb
    # sanitise whatever came back: vocabulary, rooms, groups
    room_ids = {r["id"] for r in data["rooms"]}
    group_ids = {g["id"] for g in data["student_groups"]}
    draft["room_types"] = [t for t in draft.get("room_types", []) if t in ROOM_TYPES] or fb["room_types"]
    draft["needs"] = [n for n in draft.get("needs", []) if n in NEEDS]
    draft["mandatory_for_groups"] = [g for g in draft.get("mandatory_for_groups", []) if g in group_ids]
    draft.setdefault("assumptions", [])
    if draft.get("requested_room") and draft["requested_room"] not in room_ids:
        draft["assumptions"].append(f"Room {draft['requested_room']} does not exist at CIT; the allocator will pick a real one")
        draft["requested_room"] = None
    if not draft.get("attendance") and fb.get("attendance"):
        draft["attendance"] = fb["attendance"]
    draft.pop("claims", None)
    return {"draft": draft, "source": source, "llm_error": err,
            "warnings": review(draft, data, user), "security": security_note(flags, claims, data, user)}


def security_note(flags, claims, data, user):
    if not flags and not claims:
        return None
    cap = data["role_category_cap"].get(user["role"], "club_event")
    from .bookings import priority_text
    return {"flags": flags, "claimed": claims,
            "message": ("Role and priority claims in the text were ignored. Your category comes from your login: "
                        f"{user['role_label']} can book at most a {priority_text(data, cap)}. "
                        + ("The text also contained instructions to bypass rules or change other bookings; "
                           "nothing was cancelled or approved." if flags else ""))}


def review(draft, data, user):
    """Deterministic warnings about the draft (shown above the editable form)."""
    w = []
    rooms = {r["id"]: r for r in data["rooms"]}
    rid = draft.get("requested_room")
    req = {"attendance": draft.get("attendance") or 0, "room_types": draft.get("room_types") or [],
           "needs": draft.get("needs") or []}
    if rid in rooms:
        probs = G.request_fit_problems(rooms[rid], req)
        if probs:
            ok = [r["id"] for r in sorted(data["rooms"], key=lambda r: r["capacity"]) if G.room_fits_request(r, req)]
            w.append(f"{rid} does not fit this request ({'; '.join(probs)})." +
                     (f" Rooms that do: {', '.join(ok[:4])}." if ok else ""))
    for win in draft.get("windows", []):
        if win["to_period"] > G.MAX_PERIOD:
            w.append(f"{win['day']} window ends after 22:00 (P13); only an alternative slot can be offered.")
        if win["from_period"] <= G.LAST_TEACHING and draft.get("mandatory_for_groups"):
            w.append(f"{win['day']} P{win['from_period']} is during teaching hours; compulsory groups must be free then.")
    if draft.get("series"):
        w.append(f"This is a recurring request: one booking per window ({len(draft.get('windows', []))}) will be created.")
    return w


# ---------------------------------------------------------------- faculty assistant
def parse_faculty(text, today, my_sessions):
    fb = NLP.parse_faculty(text, today=today)
    try:
        system = _prompt("faculty_assistant.md", today=today,
                         sessions="; ".join(f"{s['id']} ({s['course_id']} {s['kind']}, {s['slot']})" for s in my_sessions))
        d = FacultyIntent.model_validate(_json(_call(system, text))).model_dump()
        d["security_flags"] = fb["security_flags"]
        return d, "llm", None
    except (LLMUnavailable, ValidationError) as e:
        return fb, "fallback", str(e)[:300]


# ---------------------------------------------------------------- explanations
def phrase(question, facts, fallback_text):
    """LLM rewording of facts computed by code; template text when the LLM is unavailable."""
    try:
        text = _call(_prompt("explainer.md", question=question,
                             facts=json.dumps(facts, indent=1) if not isinstance(facts, str) else facts),
                     "Explain it.")
        return text.strip(), "llm"
    except LLMUnavailable:
        return fallback_text, "template"
