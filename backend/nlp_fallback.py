"""Deterministic English + Hinglish parser. Used when no LLM is configured or it fails,
and always used for the security checks (role/priority claims, injection attempts)."""
import re

DAYS = ["MON", "TUE", "WED", "THU", "FRI"]
DAY_WORDS = {
    "MON": ["monday", "mon", "somvar", "somvaar"],
    "TUE": ["tuesday", "tue", "tues", "mangalvar", "mangalvaar", "mangal"],
    "WED": ["wednesday", "wed", "budhvar", "budhvaar", "budh"],
    "THU": ["thursday", "thu", "thurs", "guruvar", "guruvaar", "brihaspativar"],
    "FRI": ["friday", "fri", "shukravar", "shukravaar", "shukra"],
    "SAT": ["saturday", "sat", "shanivar"],
    "SUN": ["sunday", "sun", "ravivar", "itvaar"],
}
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8}
NEEDS = [
    ("projector", r"projector|screen|ppt|slides|presentation"),
    ("pa_system", r"\bmic\b|mike|microphone|sound|speakers\b|\bpa\b|audio|music system"),
    ("stage", r"\bstage\b|manch"),
    ("computers", r"computers?\b|\bpcs?\b|computer lab|systems wala|coding"),
    ("accessible", r"wheelchair|accessible|accessibility|divyang|\bramp\b"),
    ("power_benches", r"power (points|sockets|benches)|soldering|power supply"),
    ("ac", r"\bac\b|air[- ]?condition"),
]
ROOM_TYPES = [
    ("seminar_hall", r"seminar hall|\bsh\b|seminar"),
    ("auditorium", r"auditorium|\baudi\b"),
    ("outdoor", r"\boat\b|open[- ]air|outdoor|khule mein|ground"),
    ("lab_cs", r"computer lab|cs lab|lab with computers|computer centre"),
    ("lab_elec", r"elec(tronics|trical)? lab"),
    ("lab_phy", r"physics lab"),
    ("lab_civil", r"civil lab"),
    ("lecture_hall", r"lecture hall|\blh\b|\blhc\b|lecture theatre"),
    ("classroom", r"classroom|class room|small room|any room|\broom\b"),
]
EXPLICIT_LAB_TYPES = [
    ("lab_cs", r"\b(?:computer|computing|cse|cs)\s+lab\b"),
    ("lab_elec", r"\b(?:electronics?|electrical|ece|ee)\s+lab\b"),
    ("lab_phy", r"\b(?:physics?|phy)\s+lab\b"),
    ("lab_civil", r"\b(?:civil|ce)\s+lab\b"),
    ("workshop", r"\b(?:mechanical|me|workshop)\s+lab\b|\bworkshop\b"),
]
DEPTS = {"CSE": r"cse|computer science|cs\b", "ECE": r"ece|electronics", "EE": r"\bee\b|electrical",
         "ME": r"\bme\b|mech(anical)?", "CE": r"\bce\b|civil"}
YEARS = {1: r"first|1st|pehle", 2: r"second|2nd|doosre|dusre", 3: r"third|3rd|teesre|tisre"}
CATEGORY_CLAIMS = [
    ("exam", r"\bexam\b|priority ?1\b|top priority|highest priority"),
    ("institute_event", r"institute(-| )level|institute event|official spectra|spectra main|convocation"),
    ("guest_lecture", r"guest lecture|guest speaker|industry speaker|industry talk|invited talk"),
    ("department_event", r"department event|dept event|departmental"),
]
INJECTION = [
    r"ignore (all|any|previous|the|your)? ?(rules|instructions|constraints|prompt)",
    r"(i am|i'm|im|as) (the )?(dean|admin|administrator|director|principal|hod)",
    r"mark (it|this|that)( event)? as (an? )?(exam|institute|priority)",
    r"\bcancel\b.*\b[A-Z]{2,3}\d{3}\b", r"\boverride\b", r"\bbypass\b", r"system prompt", r"developer mode",
    r"approve (it|this) (directly|automatically|yourself)", r"\bdelete\b", r"\bsudo\b",
    r"you (must|have to) approve", r"no need to check",
]
EVENT_NOUNS = r"(workshop|hackathon|quiz|seminar|talk|lecture|meeting|rehearsal|farewell|contest|screening|" \
              r"exhibition|party|open mic|session|meetup|practice|competition|ceremony|concert|jam|fest|show|demo|orientation)"


def _hour(h, mins, ampm, context):
    h = int(h)
    if ampm in ("am",):
        h = 0 if h == 12 else h
    elif ampm in ("pm",) or (ampm is None and context in ("evening", "afternoon", "night") and h < 12):
        h = h + 12 if h < 12 else h
    elif ampm is None and context == "morning":
        pass
    elif h <= 7:            # bare "6 baje" / "6 to 8": classes end at 18:00, so small numbers are pm
        h += 12
    return h + (int(mins) / 60 if mins else 0)


def hour_to_period(h):
    """Clock hour (24h) -> period that starts at that hour, None at lunch/outside."""
    h = int(h)
    if 8 <= h <= 12:
        return h - 7
    if 14 <= h <= 23:
        return h - 8
    if h == 24:
        return 16
    return None


def _context(text):
    if re.search(r"evening|shaam|sham\b|tonight|night|raat", text):
        return "evening"
    if re.search(r"after ?lunch|lunch ke baad|afternoon|dopahar|dopehar|post[- ]lunch", text):
        return "afternoon"
    if re.search(r"morning|subah|subha", text):
        return "morning"
    return None


VAGUE = {"morning": (1, 5, "morning = P1-P5 (08:00-13:00)"),
         "afternoon": (6, 9, "after lunch / afternoon = P6-P9 (14:00-18:00)"),
         "evening": (10, 13, "evening = P10-P13 (18:00-22:00)")}


def _days(text, today):
    found = []
    if re.search(r"\b(day after tomorrow|parso|parson)\b", text):
        found.append(("rel", 2))
    elif re.search(r"\b(tomorrow|kal)\b", text):
        found.append(("rel", 1))
    elif re.search(r"\b(today|aaj|tonight)\b", text):
        found.append(("rel", 0))
    for d, words in DAY_WORDS.items():
        for w in words:
            m = re.search(rf"\b{w}\b", text)
            if m:
                found.append((m.start(), d))
                break
    named = [d for pos, d in sorted(x for x in found if x[0] != "rel")]
    if re.search(r"\b(daily|every ?day|all (of )?(fest )?week|har din|rozana|roz)\b", text) and not named:
        return DAYS[:], True, None
    if named:
        series = len(named) > 1 and bool(re.search(r"\bevery\b|\bhar\b|\band\b|\baur\b", text)) \
            and not re.search(r"\bor\b|\bya\b", text)
        return list(dict.fromkeys(named)), series, None
    rel = [x for x in found if x[0] == "rel"]
    if rel:
        idx = (DAYS + ["SAT", "SUN"]).index(today) if today in DAYS + ["SAT", "SUN"] else 0
        target = (DAYS + ["SAT", "SUN"])[(idx + rel[0][1]) % 7]
        note = f"'{'kal/tomorrow' if rel[0][1] == 1 else 'relative day'}' resolved to {target} (today is {today})"
        if target in ("SAT", "SUN"):
            return ["MON"], False, note + "; that is a weekend, so Monday was assumed"
        return [target], False, note
    return [], False, None


def _times(text, ctx):
    """Returns (from_hour, to_hour_exclusive or None)."""
    t = re.sub(r"\bmidnight\b", "24", text)
    t = re.sub(r"\bnoon\b", "12 pm", t)
    rng = re.search(r"(?:from |between )?(\d{1,2})(?::(\d\d))?\s*(am|pm)?\s*(?:to|till|until|-|–|se|and)\s*(\d{1,2})(?::(\d\d))?\s*(am|pm|baje)?(?!\s*(?:hours?|hrs?|ghante|people|log|students))", t)
    if rng:
        a, am, aap, b, bm, bap = rng.groups()
        bap = None if bap == "baje" else bap
        aap = aap or (bap if bap and int(a) <= int(b) else None)
        h1 = _hour(a, am, aap, ctx)
        h2 = _hour(b, bm, bap or aap, ctx) if b != "24" else 24
        if h2 <= h1 and h2 + 12 <= 24:
            h2 += 12
        return h1, h2
    one = re.search(r"(?:at |@ ?)?(\d{1,2})(?::(\d\d))?\s*(am|pm|baje|o'?clock)", t)
    if one:
        a, am, ap = one.groups()
        return _hour(a, am, ap if ap in ("am", "pm") else None, ctx), None
    return None, None


def _duration(text):
    if re.search(r"all ?day|whole day|full day|pura din|poora din", text):
        return 9
    m = re.search(r"(\d+|" + "|".join(NUM_WORDS) + r")[\s-]*(?:straight |continuous )?(?:hours?|hrs?|hr|ghante|ghanta|ghanton)", text)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else NUM_WORDS[v]
    return None


def _attendance(text):
    t = re.sub(r"\d{1,2}(:\d\d)?\s*(am|pm|baje)", " ", text)
    m = re.search(r"(\d{2,5})\s*\+?\s*(?:people|persons|students|participants|log\b|logon|logo\b|audience|attendees|"
                  r"visitors|spectators|dancers|members|bande|seats|pax|guests|janta|participants)", t)
    if not m:
        m = re.search(r"(?:around|about|approx\.?|approximately|~|lagbhag|takriban|nearly|expecting|for)\s*(\d{2,5})\b(?!\s*(?:hours?|hrs?|ghante|pm|am|baje))", t)
    if not m:
        m = re.search(r"\b(\d{2,5})\s*(?:ke liye|logon ke)", t)
    return int(m.group(1)) if m else None


def _rooms(text, rooms):
    """Known room ids mentioned, plus unknown room-like codes (e.g. LHC-5)."""
    found, unknown = [], []
    norm = text.upper()
    for word, code in (("SEMINAR HALL", "SH"), ("LECTURE HALL", "LH"), ("CLASSROOM", "CR"), ("ROOM", "CR")):
        norm = re.sub(rf"\b{word}\s*-?\s*(\d)", rf"{code}-\1", norm)
    for r in sorted(rooms, key=lambda r: -len(r["id"])):
        rid = r["id"].upper()
        pat = re.escape(rid).replace(r"\-", "[- ]?")
        if re.search(rf"(?<![A-Z0-9]){pat}(?![0-9])", norm):
            if not any(rid in f for f in found):
                found.append(r["id"])
    if re.search(r"main auditorium|\baudi(torium)?\b", text) and "AUD" not in found and \
            sum(1 for r in rooms if r["type"] == "auditorium") == 1:
        found.append("AUD")
    for m in re.finditer(r"\b([a-z]{2,4})[- ]?(\d{1,3})\b", text):
        code = f"{m.group(1).upper()}-{m.group(2)}"
        if code.replace("-", "") not in [f.replace("-", "") for f in found] and m.group(1) in ("lhc", "lh", "sh", "cr", "lab", "lt", "hall"):
            if not any(r["id"].replace("-", "") == code.replace("-", "") for r in rooms):
                unknown.append(code)
    return found, unknown


def _groups(text, groups):
    out = [g["id"] for g in groups if re.search(rf"\b{re.escape(g['id'].lower())}\b", text)]
    for dept, dpat in DEPTS.items():
        for y, ypat in YEARS.items():
            if re.search(rf"({dpat})\s*(?:{ypat})\s*(?:year|yr)s?|(?:{ypat})\s*(?:year|yr)s?\s*(?:of\s*)?({dpat})", text):
                gid = f"{dept}-{y}"
                if any(g["id"] == gid for g in groups):
                    out.append(gid)
    for y, ypat in YEARS.items():
        if y == 1 and re.search(rf"(?:{ypat})\s*years?\b(?!\s*(?:{'|'.join(DEPTS.values())}))", text):
            out += [g["id"] for g in groups if g["year"] == 1]
    return list(dict.fromkeys(out))


def security_flags(text):
    t = text.lower()
    flags = [p for p in INJECTION if re.search(p, t, re.I)]
    claims = [c for c, p in CATEGORY_CLAIMS if re.search(p, t)]
    return flags, claims


def split_conditional(text):
    parts = re.split(r",?\s*\b(?:or if (?:it'?s |that'?s |that is |it is )?(?:busy|taken|full|booked|not available|unavailable|not free)"
                     r"(?: then)?|otherwise|else|warna|nahi to|nahi toh|if not(?: available)?(?: then)?|"
                     r"agar (?:wo|woh|ye|yeh)? ?(?:busy|khaali nahi|free nahi|nahi mila) (?:hai )?(?:to|toh))\b,?\s*", text)
    return [p for p in parts if p and p.strip()]


def parse_part(text, data, today, base=None):
    t = " " + text.lower() + " "
    out = {"assumptions": []}
    ctx = _context(t)
    days, series, note = _days(t, today)
    if note:
        out["assumptions"].append(note)
    h1, h2 = _times(t, ctx)
    dur = _duration(t) or (base or {}).get("duration_periods")
    window = None
    pm = re.search(r"\bp(\d{1,2})\b(?:\s*(?:-|to)\s*p?(\d{1,2})\b)?", t)
    if pm:
        p1 = int(pm.group(1))
        p2 = int(pm.group(2)) if pm.group(2) else p1 + (dur or 2) - 1
        window = (p1, p2)
        dur = dur or p2 - p1 + 1
    elif h1 is not None:
        p1 = hour_to_period(h1)
        if p1 is None:
            p1 = 6 if 13 <= h1 < 14 else None
            if p1:
                out["assumptions"].append("13:00 is lunch; moved the start to P6 (14:00)")
        if p1 is not None:
            if h2 is not None:
                p2 = hour_to_period(h2 - 1) if h2 - 1 != 13 else 5
                window = (p1, p2)
                if dur is None:
                    dur = max(1, p2 - p1 + 1)
            else:
                window = (p1, p1 + (dur or 2) - 1)
    elif ctx:
        a, b, label = VAGUE[ctx]
        window = (a, b)
        out["assumptions"].append(f"Vague time: assumed {label}. Edit the window if you meant something else.")
    if dur == 9 and window is None:
        window = (1, 9)
    if base:
        days = days or base.get("_days")
        window = window or base.get("_window")
    out["_days"], out["_window"], out["series"] = days, window, series
    out["duration_periods"] = dur
    rooms, unknown = _rooms(t, data["rooms"])
    out["requested_room"] = rooms[0] if rooms else None
    out["unknown_rooms"] = unknown
    return out


def parse_booking(text, data, today="MON", role="club"):
    parts = split_conditional(text)
    main = parse_part(parts[0], data, today)
    fallbacks = [parse_part(p, data, today, base=main) for p in parts[1:]]
    t = " " + text.lower() + " "
    needs = [n for n, p in NEEDS if re.search(p, t)]
    room_types = [rt for rt, p in ROOM_TYPES if re.search(p, t)]
    explicit_labs = [rt for rt, p in EXPLICIT_LAB_TYPES if re.search(p, t)]
    if explicit_labs:
        room_types = explicit_labs
    att = _attendance(t)
    room_by_id = {r["id"]: r for r in data["rooms"]}
    for rid in [main["requested_room"]] + [f["requested_room"] for f in fallbacks]:
        if rid and room_by_id[rid]["type"] not in room_types:
            room_types.append(room_by_id[rid]["type"])
    if "computers" in needs and "lab_cs" not in room_types:
        room_types.append("lab_cs")
    if "classroom" in room_types and len(room_types) > 1 and not re.search(r"classroom|class room|small room", t):
        room_types.remove("classroom")
    assumptions = list(main["assumptions"])
    if not room_types:
        n = att or 60
        room_types = (["classroom", "lecture_hall", "seminar_hall"] if n <= 90 else
                      ["lecture_hall", "seminar_hall", "auditorium"] if n <= 200 else ["auditorium", "outdoor"])
        assumptions.append(f"No venue type named; suggested {', '.join(room_types)} for {att or 'the'} attendees")
    groups = _groups(t, data["student_groups"])
    mandatory = groups if re.search(r"compulsory|mandatory|must attend|zaroori|zaruri|required for|compulsary", t) else []
    flags, claims = security_flags(text)
    follow = []
    if not main["_days"]:
        follow.append("Which day should this be on?")
    if main["_window"] is None:
        follow.append("What time should it start (and end)?")
    if att is None:
        follow.append("About how many people will attend?")
    dur = main["duration_periods"]
    if dur is None:
        dur = 2
        follow.append("How long will it run? I assumed 2 hours.")
    for u in main["unknown_rooms"]:
        assumptions.append(f"Room {u} does not exist at CIT; pick a real room or leave it to the allocator")
    win = main["_window"] or (10, 11)
    windows = [{"day": d, "from_period": win[0], "to_period": max(win[1], win[0] + dur - 1)} for d in (main["_days"] or [])]
    for w in windows:
        if w["to_period"] > 13:
            assumptions.append(f"{w['day']} window runs to P{w['to_period']}, past 22:00 (P13); it can only get an alternative")
    fbs = []
    for f in fallbacks:
        fw = f["_window"] or win
        fbs.append({"windows": [{"day": d, "from_period": fw[0], "to_period": fw[1]} for d in (f["_days"] or main["_days"] or [])],
                    "requested_room": f["requested_room"]})
    stop = r"(?:for|the|our|this|a|an|ke|baad|mark|my|of|in|on|is|me|hamara|hamari)\b"
    title_m = re.search(rf"\b((?!{stop})[a-z][a-z.&-]*(?: (?!{stop})[a-z][a-z.&-]*)?) (club|society|cell|department|dept)\b", t)
    ev = re.search(EVENT_NOUNS, t)
    title = " ".join(x for x in [f"{title_m.group(1)} {title_m.group(2)}".title() if title_m else "",
                                 ev.group(1).title() if ev else ""] if x) or "Venue booking"
    return {
        "title": title, "attendance": att, "duration_periods": dur, "windows": windows,
        "room_types": room_types, "needs": needs, "requested_room": main["requested_room"],
        "mandatory_for_groups": mandatory, "claimed_categories": claims, "fallbacks": fbs,
        "series": bool(main["series"]), "follow_up": " ".join(follow) or None,
        "assumptions": assumptions, "security_flags": flags,
    }


# ---------------------------------------------------------------- faculty assistant
def parse_faculty(text, today="MON"):
    t = " " + text.lower() + " "
    if re.search(r"\b(leave|unavailable|not available|absent|chutti|chhutti|can'?t come|cannot come|medical|conference|travel)\b", t):
        intent = "MARK_UNAVAILABILITY"
    elif re.search(r"\b(extra|additional|add|one more|ek aur|remedial|makeup|make-up)\b", t):
        intent = "ADD_EXTRA_SESSION"
    elif re.search(r"\b(move|shift|reschedule|change|prepone|postpone|earlier|later|swap|hata)\b", t):
        intent = "MOVE_SESSION"
    else:
        intent = "UNKNOWN"
    ctx = _context(t)
    days, _, _ = _days(t, today)
    sid = re.search(r"\b([a-z]{2}\d{3}(?:-[a-z0-9]+)*-[lpt]\d)\b", t)
    course = re.search(r"\b([a-z]{2}\d{3})\b", t)
    per = [int(x) for x in re.findall(r"\bp(\d{1,2})\b", t)]
    h1, h2 = _times(t, ctx)
    periods = None
    if h1 is not None and hour_to_period(h1):
        p1 = hour_to_period(h1)
        p2 = hour_to_period(h2 - 1) if h2 else p1
        periods = list(range(p1, (p2 or p1) + 1))
    elif ctx and ctx != "evening":
        a, b, _ = VAGUE[ctx]
        periods = list(range(a, b + 1))
    target_days, target_periods = None, None
    m = re.search(r"\b(?:to|into|on to|par)\b(.*)$", t)
    if intent == "MOVE_SESSION" and m:
        tail = m.group(1)
        td, _, _ = _days(" " + tail + " ", today)
        target_days = td or None
        tp = [int(x) for x in re.findall(r"\bp(\d{1,2})\b", tail)]
        tctx = _context(tail)
        if tp:
            target_periods = tp
        elif tctx and tctx != "evening":
            a, b, _ = VAGUE[tctx]
            target_periods = list(range(a, b + 1))
        if target_days and days and days[-1] in target_days and len(days) > 1:
            days = days[:-1]
    if intent == "MOVE_SESSION" and re.search(r"earlier|prepone|pehle", t) and not target_periods:
        source = per[0] if per else (periods[0] if periods else 9)
        target_periods = list(range(1, source))
    duration = _duration(t) or 1
    return {"intent": intent, "session_id": sid.group(1).upper() if sid else None,
            "course_id": course.group(1).upper() if course else None,
            "days": days, "periods": per or periods, "target_days": target_days,
            "target_periods": target_periods, "duration": duration,
            "reason": text.strip()[:120], "security_flags": security_flags(text)[0]}
