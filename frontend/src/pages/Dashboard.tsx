import { useEffect, useState } from "react";
import { Activity, CalendarCheck2, Download, GitPullRequestArrow, Play, ShieldCheck, Ticket } from "lucide-react";
import { api } from "../api";
import { useApp } from "../App";
import { BookingRecord } from "../types";
import Scoreboard from "../components/Scoreboard";
import { Alert, Badge, Button, Card, Empty, Skeleton, StatusBadge, useToast } from "../components/ui";

export default function Dashboard() {
  const { user, summary, job, trackJob, go, version } = useApp();
  const toast = useToast();
  const [bookings, setBookings] = useState<BookingRecord[] | null>(null);
  const [audit, setAudit] = useState<any[] | null>(null);
  const [util, setUtil] = useState<any[] | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    api<BookingRecord[]>("/api/bookings").then(setBookings).catch(() => setBookings([]));
    api("/api/audit").then(setAudit).catch(() => setAudit([]));
    api("/api/rooms/utilisation").then(setUtil).catch(() => setUtil([]));
  }, [version]);

  const generate = async () => {
    try { trackJob(await api("/api/admin/generate", { method: "POST" })); }
    catch (e: any) { toast("error", e.message); }
  };
  const exportSchedule = async () => {
    setExporting(true);
    try {
      const r = await api("/api/export/schedule");
      const blob = new Blob([JSON.stringify(r.schedule, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = r.written.includes("exports/schedule_after.json") ? "schedule_after.json" : "schedule.json";
      a.click();
      toast("success", `Downloaded and written to ${r.written.join(" + ")}`);
    } catch (e: any) { toast("error", e.message); }
    setExporting(false);
  };

  if (!summary) return <div className="grid gap-4 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}</div>;
  const running = job?.status === "running";

  if (!summary.has_timetable) {
    return (
      <Card>
        <Empty icon={<CalendarCheck2 className="h-10 w-10" />} title="No timetable yet">
          {user.role === "admin" ? (
            <div className="space-y-4">
              <p>One click builds the weekly timetable ({summary.sessions} sessions), the Week 8 midsem schedule and the decisions
                for all {summary.requests} Spectra week requests. The solver takes about a minute.</p>
              <Button onClick={generate} loading={running} icon={<Play className="h-4 w-4" />}>
                {running ? `Solving… ${Math.round(job?.elapsed || 0)}s` : "Generate timetable, exams & Spectra decisions"}
              </Button>
            </div>
          ) : <p>An Admin has to generate the timetable first. Log in as <b>admin1</b> and press Generate.</p>}
        </Empty>
      </Card>
    );
  }

  const sb = summary.scoreboard!;
  const pendingBookings = (bookings || []).filter((b) => b.status === "pending");
  const counts = (bookings || []).reduce<Record<string, number>>((m, b) => ({ ...m, [b.status]: (m[b.status] || 0) + 1 }), {});
  const myBookings = (bookings || []).filter((b) => b.mine);

  return (
    <div className="space-y-5">
      {user.role === "admin" && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={generate} loading={running && job?.kind === "generate"} disabled={running} icon={<Play className="h-4 w-4" />} variant="secondary">Regenerate</Button>
          <Button onClick={exportSchedule} loading={exporting} icon={<Download className="h-4 w-4" />}>Export schedule.json</Button>
          <Button variant="secondary" onClick={() => go("changes")} icon={<GitPullRequestArrow className="h-4 w-4" />}>Load change file / disruption</Button>
        </div>
      )}
      <Scoreboard sb={sb} />

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Current conflicts" subtitle="Hard-constraint check by the official validator" className="lg:col-span-1">
          {sb.hard_violations === 0 ? (
            <div className="flex items-center gap-3 text-sm text-emerald-700"><ShieldCheck className="h-8 w-8" /> No clashes in the timetable, Spectra week or exams.</div>
          ) : (
            <ul className="space-y-1.5 text-sm">{sb.violations.map((v, i) => <li key={i} className="rounded bg-rose-50 px-2 py-1 text-rose-800">[{v.rule}] {v.detail}</li>)}</ul>
          )}
          {sb.priority_inversions.length > 0 && <Alert tone="warn" title="Priority inversions">{sb.priority_inversions.join("; ")}</Alert>}
        </Card>

        <Card title="Pending approvals" subtitle="Nobody can approve their own request" actions={<Button variant="ghost" onClick={() => go("changes")}>Open</Button>}>
          <div className="space-y-2 text-sm">
            <Row label="Timetable changes waiting" value={summary.pending_changes} onClick={() => go("changes")} />
            <Row label="Venue bookings waiting" value={pendingBookings.filter((b) => b.approver).length} onClick={() => go("bookings")} />
            {pendingBookings.slice(0, 4).map((b) => (
              <div key={b.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
                <span className="truncate">{b.id} · {b.request.title}</span>
                {b.can_approve ? <Badge tone="indigo">you can approve</Badge> : <Badge>{b.approver?.replace("coordinator:", "coord ")}</Badge>}
              </div>
            ))}
          </div>
        </Card>

        <Card title="Spectra week (Week 7)" actions={<Button variant="ghost" onClick={() => go("bookings")} icon={<Ticket className="h-4 w-4" />}>Hub</Button>}>
          {!bookings ? <Skeleton className="h-20" /> : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 text-sm">
                {["approved", "alternative_offered", "rejected", "cancelled", "pending"].map((s) => (
                  <div key={s} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2"><StatusBadge status={s} /><b>{counts[s] || 0}</b></div>
                ))}
              </div>
              {myBookings.length > 0 && <p className="text-xs text-slate-500">You have {myBookings.length} request(s); see their status and reasons in the hub.</p>}
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Room utilisation" subtitle="Share of the 45 weekly teaching hours in use" actions={<Button variant="ghost" onClick={() => go("rooms")}>All rooms</Button>}>
          {!util ? <Skeleton className="h-40" /> : (
            <div className="space-y-2">
              {util.filter((u) => u.teaching_room).sort((a, b) => b.utilisation - a.utilisation).slice(0, 8).map((u) => (
                <div key={u.room_id} className="flex items-center gap-3 text-sm">
                  <span className="w-20 shrink-0 font-mono text-xs">{u.room_id}</span>
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-indigo-500" style={{ width: `${u.utilisation * 100}%` }} /></div>
                  <span className="w-12 text-right tabular-nums text-slate-600">{Math.round(u.utilisation * 100)}%</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Recent activity" subtitle="Audit log">
          {!audit ? <Skeleton className="h-40" /> : audit.length === 0 ? <p className="text-sm text-slate-500">Nothing yet.</p> : (
            <ul className="max-h-64 space-y-2 overflow-y-auto text-sm">
              {audit.slice(0, 15).map((a, i) => (
                <li key={i} className="flex gap-2"><Activity className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                  <span><b className="font-medium">{a.user}</b> <span className="text-slate-500">{a.action}</span> · {a.detail}
                    <span className="block text-xs text-slate-400">{new Date(a.at).toLocaleString()}</span></span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Row({ label, value, onClick }: { label: string; value: number; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left hover:bg-slate-50">
      <span>{label}</span><Badge tone={value ? "indigo" : "slate"}>{value}</Badge>
    </button>
  );
}
