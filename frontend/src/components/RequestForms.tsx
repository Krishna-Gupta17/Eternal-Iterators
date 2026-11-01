import { useEffect, useMemo, useState } from "react";
import { CalendarClock, CalendarPlus, Search, Send } from "lucide-react";
import { academicStarts, api, datesBetween, DAYS, dayOfDate, fmtDate, periodRange, PERIOD_TIME } from "../api";
import { useApp } from "../App";
import { MySession } from "../types";
import { Alert, Badge, Button, Card, Field, Tabs, useToast } from "./ui";

/** Check -> Confirm & submit gate: the last check is valid only for exactly the form it checked. */
function useGate() {
  const [checked, setChecked] = useState<{ key: string; res: any } | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  return {
    checked, showErrors, setShowErrors,
    fresh: (key: string) => checked?.key === key,
    ok: (key: string) => checked?.key === key && !!checked.res.available,
    set: (key: string, res: any) => setChecked({ key, res }),
    reset: () => { setChecked(null); setShowErrors(false); },
  };
}

function GateButtons({ canSubmit, busy, onCheck, onSubmit, checkLabel = "Check availability" }:
  { canSubmit: boolean; busy: string | null; onCheck: () => void; onSubmit: () => void; checkLabel?: string }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {!canSubmit && <span className="mr-auto text-xs text-slate-500">“Confirm & submit” appears after a successful check.</span>}
      <Button variant={canSubmit ? "secondary" : "primary"} onClick={onCheck} loading={busy === "check"} icon={<Search className="h-4 w-4" />}>{checkLabel}</Button>
      {canSubmit && <Button onClick={onSubmit} loading={busy === "submit"} icon={<Send className="h-4 w-4" />}>Confirm & submit</Button>}
    </div>
  );
}

const uniq = <T,>(xs: T[]) => Array.from(new Set(xs));

// ------------------------------------------------------------------ reschedule / extra class
type Mode = "reschedule" | "extra";
type AF = { faculty: string; course: string; groups: string; session: string; kind: string; room_type: string;
  date: string; start: number | ""; room: string; note: string };

export function AcademicRequestForm() {
  const { user, meta, refresh, go, version } = useApp();
  const toast = useToast();
  const gate = useGate();
  const [mode, setMode] = useState<Mode>("reschedule");
  const [mine, setMine] = useState<MySession[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const blank: AF = { faculty: user.faculty_id || "", course: "", groups: "", session: "", kind: "", room_type: "", date: "", start: "", room: "", note: "" };
  const [f, setF] = useState<AF>(blank);
  useEffect(() => { api<MySession[]>("/api/requests/my-sessions").then(setMine).catch(() => setMine([])); }, [version]);
  useEffect(() => { setF(blank); gate.reset(); }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // each choice narrows the next: faculty -> course -> group -> session/kind -> room type -> slot -> room
  const facs = uniq(mine.map((s) => s.faculty_id)).sort();
  const ofFac = mine.filter((s) => s.faculty_id === f.faculty);
  const courses = uniq(ofFac.map((s) => s.course_id)).sort();
  const ofCourse = ofFac.filter((s) => s.course_id === f.course);
  const groupSets = uniq(ofCourse.map((s) => s.groups.join("+")));
  const ofGroup = ofCourse.filter((s) => s.groups.join("+") === f.groups);
  const session = mode === "reschedule" ? ofGroup.find((s) => s.id === f.session) : undefined;
  const kinds = uniq(["lecture", "tutorial", ...ofGroup.filter((s) => s.kind === "lab").map(() => "lab")]);
  const labBase = ofGroup.find((s) => s.kind === "lab");
  const theoryTypes = uniq(ofGroup.filter((s) => s.kind !== "lab").flatMap((s) => s.room_types));
  const kind = mode === "reschedule" ? session?.kind || "" : f.kind;
  const roomTypes = mode === "reschedule" ? session?.room_types || []
    : kind === "lab" ? labBase?.room_types || [] : kind ? (theoryTypes.length ? theoryTypes : ["classroom", "lecture_hall", "auditorium"]) : [];
  const duration = mode === "reschedule" ? session?.duration || 1 : kind === "lab" ? labBase?.duration || 3 : 1;
  const groups = f.groups ? f.groups.split("+") : [];
  const size = mode === "reschedule" ? session?.size || 0
    : ofGroup[0]?.size ?? groups.reduce((n, g) => n + (meta.groups.find((x) => x.id === g)?.size || 0), 0);
  const needsAccess = mode === "reschedule" ? !!session?.needs_accessible : !!ofGroup[0]?.needs_accessible;
  const starts = kind ? academicStarts(meta.calendar, kind, duration, meta.academic_last_period) : [];
  // strict room-type match: only rooms of the chosen type that take this kind of class and seat the group
  const rooms = meta.rooms.filter((r) => r.type === f.room_type && r.allowed_uses.includes(kind) && r.capacity >= size && (!needsAccess || r.accessible))
    .sort((a, b) => a.capacity - b.capacity);
  const weekday = f.date ? dayOfDate(f.date) : "";

  const set = (patch: Partial<AF>) => {
    const next = { ...f, ...patch };
    // clear choices that no longer fit what they depend on
    const order: (keyof AF)[] = ["faculty", "course", "groups", "session", "kind", "room_type", "start", "room"];
    const changed = order.findIndex((k) => k in patch);
    if (changed >= 0) for (const k of order.slice(changed + 1)) if (!(k in patch)) (next as any)[k] = blank[k];
    setF(next);
  };
  // auto-pick when there is exactly one option
  useEffect(() => { if (!f.faculty && facs.length === 1) set({ faculty: facs[0] }); }, [facs.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (f.course && !f.groups && groupSets.length === 1) set({ groups: groupSets[0] }); }, [f.course]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (kind && !f.room_type && roomTypes.length === 1) setF((x) => ({ ...x, room_type: roomTypes[0] })); }, [kind, roomTypes.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  // required (*) fields depend on the request type
  const required: (keyof AF)[] = ["faculty", "course", "groups", mode === "reschedule" ? "session" : "kind", "room_type", "date", "start", "room"];
  const missing = required.filter((k) => f[k] === "" || f[k] === undefined);
  const dateProblem = f.date && (f.date < meta.today || !DAYS.includes(weekday));
  const err = (k: keyof AF) => gate.showErrors && (missing.includes(k) || (k === "date" && !!dateProblem));
  const body = () => ({ session_id: mode === "reschedule" ? f.session : "", date: f.date, day: weekday, start_period: Number(f.start), room_id: f.room,
    room_type: f.room_type, note: f.note,
    extra: mode === "extra" ? { course_id: f.course, kind: f.kind, groups, faculty_id: f.faculty, duration } : null });
  const key = JSON.stringify(body());

  const check = async () => {
    if (missing.length || dateProblem) { gate.setShowErrors(true); toast("error", "Fill in every field marked * (with a future weekday date) first"); return; }
    setBusy("check");
    try { gate.set(key, await api("/api/requests/check", { body: body() })); } catch (e: any) { toast("error", e.message); gate.reset(); }
    setBusy(null);
  };
  const submit = async () => {
    setBusy("submit");
    try {
      const r = await api("/api/requests/session-move", { body: body() });
      toast("success", `Request ${r.id} sent for Admin approval`);
      setF(blank); gate.reset();
      await refresh();
      go("changes");
    } catch (e: any) { toast("error", e.message); gate.reset(); }
    setBusy(null);
  };
  const res = gate.checked?.res;

  return (
    <Card title={<span className="flex items-center gap-2"><CalendarPlus className="h-4 w-4" /> Reschedule or add an extra class</span>}
      subtitle={`Classes end by ${meta.academic_cutoff} (P${meta.academic_last_period}); rooms must match the required type exactly. Fields marked * are required.`}>
      <Tabs value={mode} onChange={setMode} items={[{ id: "reschedule", label: "Reschedule class" }, { id: "extra", label: "Extra class" }]} />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field label="Faculty" required error={err("faculty")}>
          {user.role === "faculty" ? <input className="input bg-slate-50" readOnly value={`${f.faculty} · ${meta.faculty.find((x) => x.id === f.faculty)?.name ?? ""}`} />
            : <select className="input" value={f.faculty} onChange={(e) => set({ faculty: e.target.value })}>
              <option value="">Choose…</option>{facs.map((id) => <option key={id} value={id}>{id} · {meta.faculty.find((x) => x.id === id)?.name}</option>)}</select>}
        </Field>
        <Field label="Course / subject" required error={err("course")}>
          <select className="input" value={f.course} disabled={!f.faculty} onChange={(e) => set({ course: e.target.value })}>
            <option value="">Choose…</option>{courses.map((c) => <option key={c} value={c}>{c} · {ofFac.find((s) => s.course_id === c)?.title}</option>)}</select>
        </Field>
        <Field label="Student group / section" required error={err("groups")}>
          <select className="input" value={f.groups} disabled={!f.course} onChange={(e) => set({ groups: e.target.value })}>
            <option value="">Choose…</option>{groupSets.map((g) => <option key={g} value={g}>{g.replace(/\+/g, ", ")}</option>)}</select>
        </Field>
        {mode === "reschedule" ? (
          <Field label="Session to move" required error={err("session")} hint={session ? `Now ${session.slot} in ${session.room_id} · ${session.kind}, ${session.duration}h` : undefined}>
            <select className="input" value={f.session} disabled={!f.groups} onChange={(e) => set({ session: e.target.value })}>
              <option value="">Choose…</option>{ofGroup.map((s) => <option key={s.id} value={s.id} disabled={!!s.basket_slot}>{s.id} · {s.kind} · {s.slot}{s.basket_slot ? " (elective basket: ask the Admin)" : ""}</option>)}</select>
          </Field>
        ) : (
          <Field label="Class type" required error={err("kind")} hint={kind === "lab" ? `${duration}-hour lab, starts at P${(meta.calendar?.lab_start_periods ?? [2, 6]).join(" or P")}` : undefined}>
            <select className="input" value={f.kind} disabled={!f.groups} onChange={(e) => set({ kind: e.target.value })}>
              <option value="">Choose…</option>{kinds.map((k) => <option key={k} value={k}>{k}</option>)}</select>
          </Field>
        )}
        <Field label="Required room type" required error={err("room_type")}>
          <select className="input" value={f.room_type} disabled={!roomTypes.length} onChange={(e) => set({ room_type: e.target.value })}>
            <option value="">Choose…</option>{roomTypes.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select>
        </Field>
        <Field label="Target date" required error={err("date")} hint={f.date ? (DAYS.includes(weekday) ? `${weekday}, week of ${fmtDate(f.date)}` : "No classes on weekends") : undefined}>
          <input type="date" className="input" min={meta.today} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />
        </Field>
        <Field label="Time slot" required error={err("start")} hint={`Only slots ending by ${meta.academic_cutoff} are offered`}>
          <select className="input" value={f.start} disabled={!kind} onChange={(e) => set({ start: e.target.value === "" ? "" : +e.target.value })}>
            <option value="">Choose…</option>{starts.map((t) => <option key={t} value={t}>P{t}{duration > 1 ? `–P${t + duration - 1}` : ""} · {periodRange(meta.calendar, t, t + duration - 1)}</option>)}</select>
        </Field>
        <Field label="Room" required error={err("room")} hint={f.room_type ? `${rooms.length} ${f.room_type.replace("_", " ")} room(s) seat ${size}${needsAccess ? " with wheelchair access" : ""}` : "Pick the room type first"}>
          <select className="input" value={f.room} disabled={!f.room_type} onChange={(e) => set({ room: e.target.value })}>
            <option value="">Choose…</option>{rooms.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.capacity} seats{r.accessible ? " · ♿" : ""}</option>)}</select>
        </Field>
        <div className="sm:col-span-2"><Field label="Note for the Admin (optional)"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. clash with a department seminar" /></Field></div>
      </div>
      {res && gate.fresh(key) && (
        <div className="mt-4">
          {res.available ? (
            <Alert tone="success" title={<span className="flex flex-wrap items-center gap-2">Available: {f.room} · {fmtDate(f.date)} · P{f.start}{duration > 1 ? `–P${Number(f.start) + duration - 1}` : ""}
              {res.pending_queue?.length > 0 && <Badge tone="amber">{res.pending_queue.length} other pending request{res.pending_queue.length > 1 ? "s" : ""} in queue</Badge>}</span>}>
              No clash with the approved timetable, exams or confirmed bookings. Submitting sends it to an Admin; nothing changes until it is approved.
              {res.pending_queue?.length > 0 && <span className="mt-1 block text-xs">Also waiting for this slot: {res.pending_queue.join(", ")}. Approving one declines the others.</span>}
            </Alert>
          ) : (
            <Alert tone="error" title="Unavailable: this request can't be submitted">
              <ul className="list-inside list-disc">{res.violations.map((v: string) => <li key={v}>{v}</li>)}</ul>
            </Alert>
          )}
        </div>
      )}
      {res && !gate.fresh(key) && <div className="mt-4"><Alert tone="info">The form changed since the last check. Check availability again.</Alert></div>}
      <div className="mt-4"><GateButtons canSubmit={gate.ok(key)} busy={busy} onCheck={check} onSubmit={submit} /></div>
    </Card>
  );
}

// ------------------------------------------------------------------ leave
export function LeaveRequestForm() {
  const { user, meta, trackJob, go } = useApp();
  const toast = useToast();
  const gate = useGate();
  const [busy, setBusy] = useState<string | null>(null);
  const [f, setF] = useState({ faculty: user.faculty_id || "", from: "", to: "", start: 1, end: meta.academic_last_period, reason: "" });
  const last = meta.academic_last_period;
  const dates = useMemo(() => (f.from && f.to && f.to >= f.from ? datesBetween(f.from, f.to).filter((d) => DAYS.includes(dayOfDate(d))) : []), [f.from, f.to]);
  const missing = [!f.faculty && "faculty", !f.from && "from", (!f.to || f.to < f.from) && "to", !dates.length && "dates",
    f.end < f.start && "slots", !f.reason.trim() && "reason"].filter(Boolean) as string[];
  const err = (k: string) => gate.showErrors && missing.includes(k);
  const body = () => ({ faculty_id: f.faculty, dates, periods: Array.from({ length: f.end - f.start + 1 }, (_, i) => f.start + i), reason: f.reason.trim() });
  const key = JSON.stringify(body());

  const check = async () => {
    if (missing.length) { gate.setShowErrors(true); toast("error", "Fill in every field marked * first"); return; }
    setBusy("check");
    try { gate.set(key, await api("/api/requests/unavailability/check", { body: body() })); } catch (e: any) { toast("error", e.message); gate.reset(); }
    setBusy(null);
  };
  const submit = async () => {
    setBusy("submit");
    try {
      trackJob(await api("/api/requests/unavailability", { body: body() }));
      toast("info", "Computing a minimum-change proposal for the Admin…");
      gate.reset();
      go("changes");
    } catch (e: any) { toast("error", e.message); gate.reset(); }
    setBusy(null);
  };
  const res = gate.checked?.res;
  const periods = Array.from({ length: last }, (_, i) => i + 1);

  return (
    <Card title={<span className="flex items-center gap-2"><CalendarClock className="h-4 w-4" /> Leave / cancellation</span>}
      subtitle="Pick the dates; the scheduler proposes the fewest class moves for Admin approval. Fields marked * are required.">
      <div className="space-y-3">
        <Field label="Faculty" required error={err("faculty")}>
          {user.role === "faculty" ? <input className="input bg-slate-50" readOnly value={`${f.faculty} · ${meta.faculty.find((x) => x.id === f.faculty)?.name ?? ""}`} />
            : <select className="input" value={f.faculty} onChange={(e) => setF({ ...f, faculty: e.target.value })}>
              <option value="">Choose…</option>{meta.faculty.map((x) => <option key={x.id} value={x.id}>{x.id} · {x.name}</option>)}</select>}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From date" required error={err("from") || err("dates")}><input type="date" className="input" min={meta.today} value={f.from}
            onChange={(e) => setF({ ...f, from: e.target.value, to: !f.to || f.to < e.target.value ? e.target.value : f.to })} /></Field>
          <Field label="To date" required error={err("to")}><input type="date" className="input" min={f.from || meta.today} value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
        </div>
        {f.from && <p className="text-xs text-slate-500">{dates.length ? `Teaching days: ${dates.map(fmtDate).join(", ")}` : "No teaching days in that range (weekends have no classes)."}</p>}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Time slots from" required error={err("slots")}><select className="input" value={f.start} onChange={(e) => setF({ ...f, start: +e.target.value, end: Math.max(f.end, +e.target.value) })}>
            {periods.map((p) => <option key={p} value={p}>P{p} {PERIOD_TIME[p]}</option>)}</select></Field>
          <Field label="to" required error={err("slots")}><select className="input" value={f.end} onChange={(e) => setF({ ...f, end: +e.target.value })}>
            {periods.filter((p) => p >= f.start).map((p) => <option key={p} value={p}>P{p} (until {periodRange(meta.calendar, p, p).split("–")[1]})</option>)}</select></Field>
        </div>
        <Field label="Reason" required error={err("reason")}><input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} placeholder="Medical leave" /></Field>
        {res && gate.fresh(key) && (
          <Alert tone={res.affected.length ? "warn" : "success"} title={res.affected.length ? `${res.affected.length} class(es) would need to move` : "No classes affected"}>
            {res.affected.length > 0 && <ul className="list-inside list-disc">{res.affected.map((a: any) => <li key={a.id}>{a.id} ({a.course_id}) · {a.slot} · {a.room_id}</li>)}</ul>}
            {res.pending_queue?.length > 0 && <p className="mt-1 text-xs">Already in the queue for this faculty member: {res.pending_queue.join(", ")}.</p>}
            <p className="mt-1 text-xs">Submitting asks the solver for the fewest moves; an Admin approves the result.</p>
          </Alert>
        )}
        {res && !gate.fresh(key) && <Alert tone="info">The form changed since the last check. Check again.</Alert>}
        <GateButtons canSubmit={gate.ok(key)} busy={busy} onCheck={check} onSubmit={submit} checkLabel="Check impact" />
      </div>
    </Card>
  );
}
