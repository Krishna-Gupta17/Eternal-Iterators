"""Starter-pack import: a unified cit_public.json or the per-entity CSV files from data/csv/.

CSV cells that hold lists or objects are JSON (as in the starter pack). Uploaded entities
replace the same entities of a base dataset (the one currently loaded); everything is then
checked for schema and cross-references before it may replace the active state.
"""
import copy
import csv
import io
import json

from . import guardrails as G

# column -> type. "json" cells are JSON text; "opt_*" columns may be empty (-> None / default).
SCHEMA = {
    "courses": {"id": "str", "title": "str", "department": "str", "year": "int", "lecture_hours": "int",
                "tutorial_hours": "int", "lab_blocks": "int"},
    "faculty": {"id": "str", "name": "str", "department": "str", "max_hours_per_day": "int",
                "needs_accessible": "bool", "unavailable": "opt_list", "preferences": "opt_list"},
    "rooms": {"id": "str", "name": "str", "building": "opt_str", "type": "str", "capacity": "int",
              "features": "opt_list", "accessible": "bool", "allowed_uses": "list"},
    "student_groups": {"id": "str", "name": "str", "year": "int", "department": "str", "size": "int",
                       "needs_accessible": "bool"},
    "sessions": {"id": "str", "course_id": "str", "kind": "str", "groups": "list", "faculty_id": "str", "size": "int",
                 "duration": "int", "room_types": "list", "needs_accessible": "bool", "basket_slot": "opt_str",
                 "fixed_slot": "opt_json"},
    "exams": {"id": "str", "course_ids": "list", "groups": "list", "size": "int", "duration_hours": "int"},
    "booking_requests": {"id": "str", "submitted_at": "str", "requester": "str", "role": "str", "department": "opt_str",
                         "requested_category": "str", "title": "str", "attendance": "int", "duration_periods": "int",
                         "windows": "list", "room_types": "list", "needs": "opt_list", "requested_room": "opt_str",
                         "mandatory_for_groups": "opt_list", "series_id": "opt_str", "raw_text": "opt_str"},
    "cancellations": {"request_id": "str", "cancelled_at": "str", "by": "str", "note": "opt_str"},
    "room_unavailability": {"room_id": "str", "day": "str", "periods": "list", "reason": "opt_str"},
}
CORE = ("courses", "faculty", "rooms", "student_groups", "sessions")
CORE_OPTIONAL = ("exams",)
OVERLAYS = ("booking_requests", "cancellations", "room_unavailability")
# keys that only the unified JSON carries; CSV uploads take them from the base dataset
META_KEYS = ("dataset_id", "institute", "calendar", "priority_order", "role_category_cap")
REQUIRED_KEYS = META_KEYS[2:] + CORE + ("booking_requests",)


class DatasetError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


def entity_of(filename):
    stem = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].strip().lower()
    return stem if stem in SCHEMA else None


def _cell(entity, col, kind, raw, row_no, errors):
    raw = (raw or "").strip()
    where = f"{entity}.csv row {row_no}, column '{col}'"
    if kind.startswith("opt_") and raw == "":
        return [] if kind == "opt_list" else None
    try:
        if kind in ("str", "opt_str"):
            if not raw:
                raise ValueError("is empty")
            return raw
        if kind == "int":
            try:
                return int(raw)
            except ValueError:
                if float(raw).is_integer():        # "4.0" from a spreadsheet export
                    return int(float(raw))
                raise ValueError(f"'{raw}' is not a whole number")
        if kind == "bool":
            low = raw.lower()
            if low not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError(f"'{raw}' is not true/false")
            return low in ("true", "1", "yes")
        val = json.loads(raw)
        if kind in ("list", "opt_list") and not isinstance(val, list):
            raise ValueError("must be a JSON list")
        return val
    except (ValueError, json.JSONDecodeError) as e:
        errors.append(f"{where}: {e}")
        return None


def parse_csv(entity, text):
    """Rows of one starter-pack CSV as dataset records. Returns (records, errors)."""
    errors = []
    schema = SCHEMA[entity]
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    required = [c for c, k in schema.items() if not k.startswith("opt_")]
    missing = [c for c in required if c not in header]
    if missing:
        return [], [f"{entity}.csv is missing column(s) {', '.join(missing)} (expected: {', '.join(schema)})"]
    out = []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        out.append({c: _cell(entity, c, k, row.get(c), i, errors) for c, k in schema.items()})
    return out, errors


def _check_entity(entity, items, errors):
    if not isinstance(items, list):
        errors.append(f"'{entity}' must be a list")
        return False
    need = [c for c, k in SCHEMA[entity].items() if not k.startswith("opt_")]
    for i, x in enumerate(items):
        if not isinstance(x, dict):
            errors.append(f"{entity}[{i}] is not an object")
            return False
        miss = [c for c in need if c not in x]
        if miss:
            errors.append(f"{entity}[{i}] ({x.get('id', x.get('request_id', '?'))}) is missing {', '.join(miss)}")
    return True


def validate(data):
    """Schema + cross-reference check. Returns (errors, warnings); errors block the upload."""
    errors, warns = [], []
    for k in REQUIRED_KEYS:
        if k not in data:
            errors.append(f"missing '{k}'")
    if errors:
        return errors, warns
    for ent in SCHEMA:
        if ent in data and not _check_entity(ent, data[ent], errors):
            return errors, warns
    if errors:
        return errors[:40], warns
    cal = data["calendar"]
    if not isinstance(cal.get("days"), list) or not isinstance(cal.get("periods"), list):
        return ["calendar needs 'days' and 'periods' lists"], warns
    days = set(cal["days"])
    for ent in ("courses", "faculty", "rooms", "student_groups", "sessions", "exams", "booking_requests"):
        seen = set()
        for x in data.get(ent, []):
            if x["id"] in seen:
                errors.append(f"{ent}: duplicate id {x['id']}")
            seen.add(x["id"])
    rooms = {r["id"]: r for r in data["rooms"]}
    room_types = {r["type"] for r in data["rooms"]}
    fac = {f["id"] for f in data["faculty"]}
    groups = {g["id"] for g in data["student_groups"]}
    courses = {c["id"] for c in data["courses"]}
    reqs = {b["id"] for b in data["booking_requests"]}
    last = G.academic_last_period(data)
    for s in data["sessions"]:
        sid = s["id"]
        if s["kind"] not in G.SESSION_KINDS:
            errors.append(f"session {sid}: kind '{s['kind']}' is not lecture/tutorial/lab")
        if s["faculty_id"] not in fac:
            errors.append(f"session {sid}: unknown faculty {s['faculty_id']}")
        if s["course_id"] not in courses:
            errors.append(f"session {sid}: unknown course {s['course_id']}")
        for g in s["groups"]:
            if g not in groups:
                errors.append(f"session {sid}: unknown student group {g}")
        if not s["room_types"]:
            errors.append(f"session {sid}: room_types is empty")
        fits = [r for r in data["rooms"] if G.room_fits_session(r, s)]
        if not fits:
            errors.append(f"session {sid}: no room is of type {'/'.join(s['room_types'])}, takes {s['kind']}s and seats {s['size']}")
        if not isinstance(s["duration"], int) or s["duration"] < 1:
            errors.append(f"session {sid}: duration must be a positive integer")
        fx = s.get("fixed_slot")
        if fx:
            if not isinstance(fx, dict) or fx.get("day") not in days or not isinstance(fx.get("start_period"), int):
                errors.append(f"session {sid}: fixed_slot must be {{\"day\": ..., \"start_period\": ...}}")
            elif fx["start_period"] + s["duration"] - 1 > last:
                errors.append(f"session {sid}: fixed slot {fx['day']} P{fx['start_period']} ends after the "
                              f"{G.ACADEMIC_CUTOFF} cutoff for academic classes")
    for f in data["faculty"]:
        for u in f.get("unavailable") or []:
            if u.get("day") not in days:
                errors.append(f"faculty {f['id']}: unavailable day {u.get('day')} is not in the calendar")
    for u in data.get("room_unavailability", []):
        if u["room_id"] not in rooms:
            errors.append(f"room_unavailability: unknown room {u['room_id']}")
        if u["day"] not in days:
            errors.append(f"room_unavailability: day {u['day']} is not in the calendar")
    for e in data.get("exams", []):
        for c in e["course_ids"]:
            if c not in courses:
                errors.append(f"exam {e['id']}: unknown course {c}")
        for g in e["groups"]:
            if g not in groups:
                errors.append(f"exam {e['id']}: unknown student group {g}")
    order = data["priority_order"]
    for b in data["booking_requests"]:
        if b["role"] not in data["role_category_cap"]:
            errors.append(f"booking {b['id']}: unknown role {b['role']}")
        if b["requested_category"] not in order:
            warns.append(f"booking {b['id']}: category {b['requested_category']} is not in priority_order (treated as lowest)")
        for w in b["windows"]:
            if w.get("day") not in days:
                errors.append(f"booking {b['id']}: window day {w.get('day')} is not in the calendar")
        for g in b.get("mandatory_for_groups") or []:
            if g not in groups:
                errors.append(f"booking {b['id']}: unknown student group {g}")
        if not set(b["room_types"]) & room_types:
            warns.append(f"booking {b['id']}: no room is of type {'/'.join(b['room_types'])}; it will be rejected")
        if b.get("requested_room") and b["requested_room"] not in rooms:
            warns.append(f"booking {b['id']}: requested room {b['requested_room']} does not exist")
    for c in data.get("cancellations", []):
        if c["request_id"] not in reqs:
            warns.append(f"cancellation for unknown request {c['request_id']}")
    return errors[:40], warns[:40]


def build(files, base, clear_missing=False):
    """files: [(filename, bytes)]. Returns (data, info) or raises DatasetError.

    One .json file is a whole dataset. CSV files replace the matching entities of `base`;
    with `clear_missing`, exams and the operational overlays that were not uploaded are emptied
    (so stale requests for the old rooms/groups don't linger)."""
    if not files:
        raise DatasetError(["Choose a cit_public.json file or one or more starter-pack CSV files"])
    jsons = [f for f in files if f[0].lower().endswith(".json")]
    csvs = [f for f in files if f[0].lower().endswith(".csv")]
    other = [f[0] for f in files if f not in jsons and f not in csvs]
    if other:
        raise DatasetError([f"Unsupported file type: {', '.join(other)} (use .json or .csv)"])
    if jsons and (csvs or len(jsons) > 1):
        raise DatasetError(["Upload either one unified .json dataset or a set of .csv files, not both"])
    if jsons:
        name, raw = jsons[0]
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise DatasetError([f"{name} is not valid JSON: {e}"])
        if not isinstance(data, dict):
            raise DatasetError([f"{name} must contain a JSON object in the cit_public.json format"])
        data.setdefault("cancellations", [])
        data.setdefault("room_unavailability", [])
        data.setdefault("exams", [])
        info = {"mode": "json", "files": [name], "replaced": [k for k in SCHEMA if k in data], "kept": []}
    else:
        data = copy.deepcopy(base)
        errors, replaced, seen = [], [], {}
        for name, raw in csvs:
            ent = entity_of(name)
            if ent is None:
                errors.append(f"{name}: not a starter-pack CSV (expected one of {', '.join(f'{k}.csv' for k in SCHEMA)})")
                continue
            if ent in seen:
                errors.append(f"{name}: {ent}.csv uploaded twice ({seen[ent]})")
                continue
            seen[ent] = name
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                errors.append(f"{name} is not UTF-8 text")
                continue
            rows, errs = parse_csv(ent, text)
            errors += errs
            data[ent] = rows
            replaced.append(ent)
        if errors:
            raise DatasetError(errors[:40])
        cleared = []
        if clear_missing:
            for ent in CORE_OPTIONAL + OVERLAYS:
                if ent not in replaced and data.get(ent):
                    data[ent] = []
                    cleared.append(ent)
        info = {"mode": "csv", "files": [n for n, _ in csvs], "replaced": replaced, "cleared": cleared,
                "kept": [k for k in SCHEMA if k not in replaced and k not in cleared and data.get(k)],
                "missing_core": [k for k in CORE if k not in replaced]}
    errors, warns = validate(data)
    if errors:
        raise DatasetError(errors)
    info.update(warnings=warns, dataset_id=data.get("dataset_id"),
                counts={k: len(data.get(k, [])) for k in SCHEMA})
    return data, info
