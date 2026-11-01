import { useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, FileUp, GitCompareArrows, Loader2, RefreshCw, XCircle } from "lucide-react";
import { api, DAYS, fmtDate, PERIOD_TIME, requestLabel } from "../api";
import { useApp } from "../App";
import { BookingRecord, ChangeItem, DiffRow } from "../types";
import { Alert, Badge, Button, Card, confirmToast, cx, Empty, Field, Skeleton, StatusBadge, Tabs, useToast } from "../components/ui";

export default function Changes() {
  const { user, version, job, refresh, trackJob } = useApp();
  const toast = useToast();
  const [items, setItems] = useState<ChangeItem[] | null>(null);
  const [bookings, setBookings] = useState<BookingRecord[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    api<ChangeItem[]>("/api/changes").then(setItems).catch(() => setItems([]));
    api<BookingRecord[]>("/api/bookings").then(setBookings).catch(() => setBookings([]));
  }, [version]);

  const decide = async (id: string, action: "approve" | "reject" | "recompute") => {
    if (action === "recompute") {
      setBusy(id + action);
      try { trackJob(await api(`/api/changes/${id}/recompute`, { method: "POST" })); toast("info", "Recomputing against the live timetable…"); await refresh(); }
      catch (e: any) { toast("error", e.message); }
      setBusy(null);
      return;
    }
    const execute = async (extraBody?: any) => {
      setBusy(id + action);
      try {
        await api(`/api/changes/${id}/${action}`, { method: "POST", body: extraBody });
        toast("success", action === "approve" ? "Change applied to the timetable" : "Change rejected");
        await refresh();
      } catch (e: any) { toast("error", e.message); }
      setBusy(null);
    };

    if (action === "reject") {
      const note = prompt("Why reject this change? (optional)");
      if (note === null) return;
      void execute({ note });
    } else {
      confirmToast({
        title: "Apply this change to the live timetable?",
        confirmLabel: "Apply",
        onConfirm: () => execute(),
      });
    }
  };

  const decideBooking = async (id: string, action: "approve" | "reject" | "cancel") => {
    const execute = async (extraBody?: Record<string, string>) => {
      setBusy(id + action);
      try {
        await api(`/api/bookings/${id}/${action}`, { method: "POST", body: extraBody });
        toast("success", `Booking ${action}d`);
        await refresh();
      } catch (e: any) { toast("error", e.message); }
      setBusy(null);
    };

    if (action === "reject") {
      const note = prompt("Why reject this booking? (optional)");
      if (note === null) return;
      void execute({ reason: note });
    } else {
      confirmToast({
        title: `${action === "approve" ? "Approve" : action === "cancel" ? "Cancel" : "Reject"} this booking?`,
        confirmLabel: action === "approve" ? "Approve" : action === "cancel" ? "Cancel" : "Reject",
        onConfirm: () => execute(),
      });
    }
  };

  return (
    <div className="space-y-5">
      {user.role === "admin" && <DisruptionPanel />}
      {job?.status === "running" && job.kind === "reschedule" && (
        <Alert tone="info" title="Computing a minimum-change fix…"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />
          The solver minimises (1000 × sessions moved + soft penalty). {Math.round(job.elapsed || 0)}s so far.</Alert>)}
      <Card title="Pending venue booking requests"
        subtitle="Requests are visible to every role. Only the assigned coordinator or Admin can approve; requesters can cancel their own pending requests.">
        {!bookings ? <Skeleton className="h-32" /> : bookings.filter((b) => b.status === "pending").length === 0 ? (
          <Empty icon={<GitCompareArrows className="h-10 w-10" />} title="No pending venue requests">New booking requests will appear here for review.</Empty>
        ) : <div className="space-y-3">{bookings.filter((b) => b.status === "pending").map((b) =>
          <BookingApprovalCard key={b.id} booking={b} busy={busy} decide={decideBooking} />)}</div>}
      </Card>
      <Card title={user.role === "admin" ? "Proposed timetable changes" : "My change requests"}
        subtitle="Nothing reaches the live timetable until an Admin approves it. The requester can never approve their own change.">
        {!items ? <Skeleton className="h-48" /> : items.length === 0 ? (
          <Empty icon={<GitCompareArrows className="h-10 w-10" />} title="No changes yet">
            {user.role === "admin" ? "Upload a change file or simulate a disruption above." : "Use the Faculty assistant to request a move, an extra session or leave."}
          </Empty>
        ) : <div className="space-y-4">{items.map((it) => <ChangeCard key={it.id} it={it} busy={busy} decide={decide} />)}</div>}
      </Card>
    </div>
  );
}

function BookingApprovalCard({ booking: b, busy, decide }: {
  booking: BookingRecord;
  busy: string | null;
  decide: (id: string, action: "approve" | "reject" | "cancel") => void;
}) {
  const { meta, user, explain } = useApp();
  const windows = b.request.windows.map((w) => `${w.date ? fmtDate(w.date) : w.day} P${w.from_period}-${w.to_period}`).join(" or ");
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/30 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium text-slate-900"><span className="font-mono text-xs text-slate-500">{b.id}</span> · {b.request.title}</p>
          <p className="text-xs text-slate-500">{b.request.requester} · {b.request.attendance} people · {b.request.duration_periods}h · {windows}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <Badge tone="indigo">P{b.priority_rank} {meta.priority_labels[b.effective_category]}</Badge>
          <StatusBadge status={b.status} />
        </div>
      </div>
      {b.placement && <p className="mt-2 text-sm"><b>{b.placement.room_id}</b> · {b.placement.day} P{b.placement.start_period}-{b.placement.end_period}
        <span className="text-slate-500"> ({PERIOD_TIME[b.placement.start_period]}–{b.placement.end_period >= 13 ? "22:00" : PERIOD_TIME[b.placement.end_period + 1] ?? ""})</span></p>}
      <p className="mt-1 text-sm text-slate-600">{b.reason || `Waiting for ${b.approver === "admin" ? "Admin" : "the assigned coordinator or Admin"}.`}</p>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {b.can_approve && <>
          <Button variant="danger" loading={busy === b.id + "reject"} onClick={() => decide(b.id, "reject")}>Reject</Button>
          <Button variant="success" loading={busy === b.id + "approve"} onClick={() => decide(b.id, "approve")}>Approve</Button>
        </>}
        {(b.mine || user.role === "admin") && <Button variant="ghost" loading={busy === b.id + "cancel"} onClick={() => decide(b.id, "cancel")}>Cancel request</Button>}
        <Button variant="ghost" onClick={() => explain({ kind: "booking", id: b.id })}>Explain</Button>
      </div>
    </div>
  );
}

function ChangeCard({ it, busy, decide }: { it: ChangeItem; busy: string | null; decide: (id: string, a: "approve" | "reject" | "recompute") => void }) {
  const { user } = useApp();
  const [open, setOpen] = useState(["pending", "infeasible", "outdated"].includes(it.status));
  const d = it.diff;
  return (
    <div className={cx("rounded-xl border", it.status === "pending" ? "border-indigo-200" : it.status === "infeasible" ? "border-rose-200" : "border-slate-200")}>
      <button className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-3 text-left" onClick={() => setOpen(!open)}>
        <div className="min-w-0">
          <p className="font-medium text-slate-900"><span className="font-mono text-xs text-slate-500">{it.id}</span> · {it.request ? requestLabel(it.request) : it.change_summary.join("; ") || "manual move"}</p>
          <p className="text-xs text-slate-500">{it.source} · by {it.created_by} · {new Date(it.created_at).toLocaleString()}{it.decided_by ? ` · decided by ${it.decided_by}` : ""}</p>
          {it.effective_date && <p className="text-xs text-slate-500">Target date: {it.effective_date}</p>}
        </div>
        <div className="flex items-center gap-2">
          {it.moved_count !== undefined && <Badge tone="indigo">{it.moved_count} moved</Badge>}
          <StatusBadge status={it.status} />
        </div>
      </button>
      {open && (
        <div className="space-y-4 border-t border-slate-100 px-4 py-4">
          {it.note && <Alert tone={it.status === "rejected" ? "error" : it.status === "outdated" ? "warn" : "info"}>{it.note}</Alert>}
          {it.queue_note && it.status === "pending" && <Alert tone="warn" title="Competing requests in the queue">{it.queue_note}</Alert>}
          {it.status === "infeasible" && (
            <Alert tone="error" title="No valid fix exists. These constraints conflict:">
              <ul className="list-inside list-disc space-y-0.5">{(it.conflicts || []).map((c) => <li key={c}>{c}</li>)}</ul>
            </Alert>)}
          {it.score_before && it.score_after && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Delta label="Hard violations" a={it.score_before.hard} b={it.score_after.hard} />
              <Delta label="Soft penalty" a={it.score_before.soft} b={it.score_after.soft} />
              <Delta label="Booking score" a={it.score_before.booking ?? 0} b={it.score_after.booking ?? 0} higherBetter />
              <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Sessions moved</p><p className="text-xl font-bold">{it.moved_count}</p>
                <p className="text-xs text-slate-500">{d?.moved.filter((m) => m.change === "room only").length} room-only</p></div>
            </div>)}
          {it.released_bookings?.length > 0 && <Alert tone="warn">Bookings in rooms that close will be re-placed or rejected with a reason: {it.released_bookings.join(", ")}</Alert>}
          {it.exam_conflicts && it.exam_conflicts.length > 0 && <Alert tone="warn">Exams in closed rooms will be re-planned: {it.exam_conflicts.join(", ")}</Alert>}
          {d && (d.moved.length + d.added.length + d.removed.length > 0) && <DiffTable d={d} />}
          {it.can_approve && it.status === "pending" && (
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" icon={<XCircle className="h-4 w-4" />} loading={busy === it.id + "reject"} onClick={() => decide(it.id, "reject")}>Reject</Button>
              <Button variant="success" icon={<CheckCircle2 className="h-4 w-4" />} loading={busy === it.id + "approve"} onClick={() => decide(it.id, "approve")}>Approve & apply</Button>
            </div>)}
          {!it.can_approve && it.status === "pending" && <p className="text-right text-xs text-slate-500">Waiting for {it.requested_by ? "another" : "an"} Admin to approve.</p>}
          {it.status === "outdated" && user.role === "admin" && (
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" icon={<XCircle className="h-4 w-4" />} loading={busy === it.id + "reject"} onClick={() => decide(it.id, "reject")}>Reject</Button>
              <Button icon={<RefreshCw className="h-4 w-4" />} loading={busy === it.id + "recompute"} onClick={() => decide(it.id, "recompute")}>Recompute proposal</Button>
            </div>)}
        </div>
      )}
    </div>
  );
}

function Delta({ label, a, b, higherBetter }: { label: string; a: number; b: number; higherBetter?: boolean }) {
  const diff = b - a;
  const good = diff === 0 ? null : higherBetter ? diff > 0 : diff < 0;
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="flex items-center gap-1.5 text-xl font-bold tabular-nums">{a}<ArrowRight className="h-4 w-4 text-slate-400" />{b}</p>
      <p className={cx("text-xs", good === null ? "text-slate-500" : good ? "text-emerald-600" : "text-rose-600")}>{diff === 0 ? "no change" : `${diff > 0 ? "+" : ""}${diff}`}</p>
    </div>
  );
}

function slot(x?: { day: string; start_period: number; room_id: string }) {
  return x ? `${x.day} P${x.start_period} · ${x.room_id}` : "–";
}

function DiffTable({ d }: { d: { moved: DiffRow[]; added: DiffRow[]; removed: DiffRow[] } }) {
  const rows = [...d.added.map((r) => ({ ...r, tag: "added" })), ...d.moved.map((r) => ({ ...r, tag: r.change })), ...d.removed.map((r) => ({ ...r, tag: "removed" }))];
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr><th className="px-3 py-2">Session</th><th className="px-3">Groups · faculty</th><th className="px-3">Before</th><th className="px-3" /><th className="px-3">After</th><th className="px-3">Change</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const timeChanged = r.old && r.new && (r.old.day !== r.new.day || r.old.start_period !== r.new.start_period);
            return (
              <tr key={r.session_id} className="border-t border-slate-100">
                <td className="px-3 py-2"><span className="font-mono text-xs font-semibold">{r.session_id}</span><span className="block text-xs text-slate-500">{r.course_id} {r.kind}</span></td>
                <td className="px-3 text-xs text-slate-600">{r.groups?.join(", ")}{r.faculty_id ? ` · ${r.faculty_id}` : ""}</td>
                <td className="px-3"><span className={cx("rounded px-1.5 py-0.5 text-xs", r.old && "bg-rose-50 text-rose-800 line-through decoration-rose-300")}>{slot(r.old)}</span></td>
                <td className="px-1 text-slate-400"><ArrowRight className="h-4 w-4" /></td>
                <td className="px-3"><span className={cx("rounded px-1.5 py-0.5 text-xs", r.new && "bg-emerald-50 font-medium text-emerald-800")}>{slot(r.new)}</span></td>
                <td className="px-3"><Badge tone={r.tag === "added" ? "green" : r.tag === "removed" ? "red" : timeChanged ? "amber" : "sky"}>{r.tag}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DisruptionPanel() {
  const { meta, trackJob, job } = useApp();
  const academic = Array.from({ length: meta.academic_last_period }, (_, i) => i + 1);
  const toast = useToast();
  const [tab, setTab] = useState<"file" | "faculty" | "room" | "session">("file");
  const [file, setFile] = useState<File | null>(null);
  const [days, setDays] = useState<string[]>(["MON"]);
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(9);
  const [fac, setFac] = useState(meta.faculty[0]?.id);
  const [room, setRoom] = useState(meta.rooms[0]?.id);
  const [reason, setReason] = useState("");
  const [ns, setNs] = useState({ course: meta.courses[0]?.id, kind: "tutorial", faculty: meta.faculty[0]?.id, groups: [] as string[], size: 60, duration: 1, fixed_day: "", fixed_start: 1 });
  const running = job?.status === "running";
  const periods = Array.from({ length: to - from + 1 }, (_, i) => from + i);

  const send = async (fn: () => Promise<any>) => {
    try { trackJob(await fn()); toast("info", "Solving for the fewest moves…"); } catch (e: any) { toast("error", e.message); }
  };
  const submit = () => {
    if (tab === "file") {
      if (!file) return toast("error", "Choose a change file first");
      const fd = new FormData();
      fd.append("file", file);
      return send(() => api("/api/changes/upload", { form: fd }));
    }
    let change: any;
    if (tab === "faculty") change = { type: "faculty_unavailable", faculty_id: fac, days, periods, reason: reason || "Faculty leave" };
    else if (tab === "room") change = { type: "room_unavailable", room_id: room, days, periods, reason: reason || "Room closed" };
    else {
      if (!ns.groups.length) return toast("error", "Pick at least one student group");
      const n = Math.floor(Math.random() * 900 + 100);
      change = { type: "add_session", session: { id: `${ns.course}-N${n}`, course_id: ns.course, kind: ns.kind, groups: ns.groups, faculty_id: ns.faculty,
        size: ns.size, duration: ns.duration, room_types: ["classroom", "lecture_hall", "auditorium"], needs_accessible: false, basket_slot: null,
        fixed_slot: ns.fixed_day ? { day: ns.fixed_day, start_period: ns.fixed_start } : null } };
    }
    return send(() => api("/api/changes/simulate", { body: change }));
  };

  return (
    <Card title="Disruption & rescheduling centre" subtitle="Load a change file in the starter-pack format or simulate one. The solver proposes the fix that moves the fewest sessions.">
      <Tabs value={tab} onChange={setTab} items={[{ id: "file", label: "Upload change file" }, { id: "faculty", label: "Faculty leave" }, { id: "room", label: "Room closure" }, { id: "session", label: "New session" }]} />
      <div className="mt-4 space-y-3">
        {tab === "file" && (
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-slate-300 px-4 py-8 text-center hover:border-indigo-400">
            <FileUp className="h-8 w-8 text-slate-400" />
            <span className="text-sm font-medium">{file ? file.name : "Choose sample_disruption.json or any change file"}</span>
            <span className="text-xs text-slate-500">{"{\"changes\": [faculty_unavailable | room_unavailable | add_faculty | add_session | remove_session]}"}</span>
            <input type="file" accept=".json,application/json" className="hidden" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>
        )}
        {(tab === "faculty" || tab === "room") && (
          <div className="grid gap-3 sm:grid-cols-2">
            {tab === "faculty" ? (
              <Field label="Faculty"><select className="input" value={fac} onChange={(e) => setFac(e.target.value)}>{meta.faculty.map((f) => <option key={f.id} value={f.id}>{f.id} · {f.name}</option>)}</select></Field>
            ) : (
              <Field label="Room"><select className="input" value={room} onChange={(e) => setRoom(e.target.value)}>{meta.rooms.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.name}</option>)}</select></Field>
            )}
            <Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={tab === "faculty" ? "Medical leave" : "Water leak"} /></Field>
            <Field label="Days"><div className="flex flex-wrap gap-1.5">{DAYS.map((d) => (
              <button type="button" key={d} onClick={() => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d])}
                className={cx("rounded-full px-3 py-1 text-xs ring-1 ring-inset", days.includes(d) ? "bg-indigo-600 text-white ring-indigo-600" : "ring-slate-300")}>{d}</button>))}</div></Field>
            <Field label="Periods"><div className="flex items-center gap-2">
              <select className="input" value={from} onChange={(e) => setFrom(+e.target.value)}>{[1, 2, 3, 4, 5, 6, 7, 8, 9].map((p) => <option key={p} value={p}>P{p}</option>)}</select>
              <span className="text-xs">to</span>
              <select className="input" value={to} onChange={(e) => setTo(+e.target.value)}>{[1, 2, 3, 4, 5, 6, 7, 8, 9].filter((p) => p >= from).map((p) => <option key={p} value={p}>P{p}</option>)}</select></div></Field>
          </div>
        )}
        {tab === "session" && (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Course"><select className="input" value={ns.course} onChange={(e) => setNs({ ...ns, course: e.target.value })}>{meta.courses.map((c) => <option key={c.id} value={c.id}>{c.id} · {c.title}</option>)}</select></Field>
            <Field label="Faculty"><select className="input" value={ns.faculty} onChange={(e) => setNs({ ...ns, faculty: e.target.value })}>{meta.faculty.map((f) => <option key={f.id} value={f.id}>{f.id} · {f.name}</option>)}</select></Field>
            <Field label="Kind"><select className="input" value={ns.kind} onChange={(e) => setNs({ ...ns, kind: e.target.value })}><option value="lecture">lecture</option><option value="tutorial">tutorial</option></select></Field>
            <Field label="Students"><input className="input" type="number" value={ns.size} onChange={(e) => setNs({ ...ns, size: +e.target.value })} /></Field>
            <Field label="Hours"><input className="input" type="number" min={1} max={2} value={ns.duration} onChange={(e) => setNs({ ...ns, duration: +e.target.value })} /></Field>
            <Field label="Fixed slot (optional)"><div className="flex gap-2">
              <select className="input" value={ns.fixed_day} onChange={(e) => setNs({ ...ns, fixed_day: e.target.value })}><option value="">none</option>{DAYS.map((d) => <option key={d}>{d}</option>)}</select>
              <select className="input" value={ns.fixed_start} disabled={!ns.fixed_day} onChange={(e) => setNs({ ...ns, fixed_start: +e.target.value })}>{academic.filter((p) => p + ns.duration - 1 <= meta.academic_last_period).map((p) => <option key={p} value={p}>P{p}</option>)}</select></div></Field>
            <div className="sm:col-span-3"><Field label="Student groups"><div className="flex flex-wrap gap-1.5">{meta.groups.map((g) => (
              <button type="button" key={g.id} onClick={() => setNs({ ...ns, groups: ns.groups.includes(g.id) ? ns.groups.filter((x) => x !== g.id) : [...ns.groups, g.id] })}
                className={cx("rounded-full px-2.5 py-1 text-xs ring-1 ring-inset", ns.groups.includes(g.id) ? "bg-indigo-600 text-white ring-indigo-600" : "ring-slate-300")}>{g.id}</button>))}</div></Field></div>
          </div>
        )}
        <div className="flex justify-end"><Button onClick={submit} loading={running} disabled={running}>Propose minimum-change fix</Button></div>
      </div>
    </Card>
  );
}
