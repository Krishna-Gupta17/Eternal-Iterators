import { useEffect, useState } from "react";
import { Accessibility, Search } from "lucide-react";
import { api, DAYS, PERIOD_TIME, PERIODS } from "../api";
import { useApp } from "../App";
import { Badge, Button, Card, Empty, Field, Skeleton, useToast } from "../components/ui";

export default function Rooms() {
  const { version, meta, summary } = useApp();
  const toast = useToast();
  const [util, setUtil] = useState<any[] | null>(null);
  const [q, setQ] = useState({ day: "MON", start: 2, duration: 1, min_capacity: 60, room_type: "", accessible: false, week7: false });
  const [free, setFree] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { api("/api/rooms/utilisation").then(setUtil).catch(() => setUtil([])); }, [version]);

  const search = async () => {
    setLoading(true);
    try {
      const p = new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)]));
      setFree(await api(`/api/rooms/free?${p}`));
    } catch (e: any) { toast("error", e.message); }
    setLoading(false);
  };
  useEffect(() => { search(); }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="grid gap-5 xl:grid-cols-[380px_1fr]">
      <Card title="Find a free room" subtitle="Checks the timetable, maintenance closures and (in Spectra view) approved bookings">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Day"><select className="input" value={q.day} onChange={(e) => setQ({ ...q, day: e.target.value })}>{DAYS.map((d) => <option key={d}>{d}</option>)}</select></Field>
          <Field label="Start"><select className="input" value={q.start} onChange={(e) => setQ({ ...q, start: +e.target.value })}>
            {PERIODS.map((p) => <option key={p} value={p}>P{p} · {PERIOD_TIME[p]}</option>)}</select></Field>
          <Field label="Hours"><input className="input" type="number" min={1} max={9} value={q.duration} onChange={(e) => setQ({ ...q, duration: +e.target.value })} /></Field>
          <Field label="Min seats"><input className="input" type="number" min={0} value={q.min_capacity} onChange={(e) => setQ({ ...q, min_capacity: +e.target.value })} /></Field>
          <div className="col-span-2"><Field label="Room type"><select className="input" value={q.room_type} onChange={(e) => setQ({ ...q, room_type: e.target.value })}>
            <option value="">Any type</option>{meta.room_types.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></Field></div>
          <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" className="accent-indigo-600" checked={q.accessible} onChange={(e) => setQ({ ...q, accessible: e.target.checked })} /> Wheelchair accessible only</label>
          <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" className="accent-indigo-600" checked={q.week7} onChange={(e) => setQ({ ...q, week7: e.target.checked })} /> Spectra week (include approved bookings)</label>
        </div>
        <Button className="mt-4 w-full" onClick={search} loading={loading} icon={<Search className="h-4 w-4" />}>Find free rooms</Button>
        <div className="mt-4">
          {!free ? <Skeleton className="h-32" /> : free.length === 0 ? <Empty title="No room is free">Try another time, fewer seats or a different type.</Empty> : (
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {free.map((r) => (
                <li key={r.room_id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <div className="min-w-0"><p className="font-medium">{r.room_id} <span className="font-normal text-slate-500">{r.name}</span></p>
                    <p className="truncate text-xs text-slate-500">{r.type.replace("_", " ")} · {r.features.join(", ")}</p></div>
                  <div className="flex shrink-0 items-center gap-1.5">{r.accessible && <Accessibility className="h-4 w-4 text-sky-600" aria-label="accessible" />}<Badge>{r.capacity}</Badge></div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      <Card title="Room utilisation" subtitle={`Teaching hours used out of ${summary?.teaching_hours ?? ""} per week (classes end by ${meta.academic_cutoff}) · average fill = students ÷ seats`}>
        {!util ? <Skeleton className="h-96" /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">Room</th><th className="pr-3">Type</th><th className="pr-3 text-right">Seats</th><th className="w-[40%] pr-3">Utilisation</th><th className="text-right">Avg fill</th></tr></thead>
              <tbody>
                {util.map((u) => (
                  <tr key={u.room_id} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 pr-3"><span className="font-mono text-xs font-semibold">{u.room_id}</span>{u.hours_closed > 0 && <Badge tone="amber" className="ml-2">closed {u.hours_closed}h</Badge>}</td>
                    <td className="pr-3 text-slate-600">{u.type.replace("_", " ")}</td>
                    <td className="pr-3 text-right tabular-nums">{u.capacity}</td>
                    <td className="pr-3">
                      {u.teaching_room ? (
                        <div className="flex items-center gap-2">
                          <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                            <div className={`h-full rounded-full ${u.utilisation > 0.75 ? "bg-rose-500" : u.utilisation > 0.4 ? "bg-indigo-500" : "bg-sky-400"}`} style={{ width: `${u.utilisation * 100}%` }} /></div>
                          <span className="w-10 text-right text-xs tabular-nums text-slate-600">{Math.round(u.utilisation * 100)}%</span>
                        </div>
                      ) : <span className="text-xs text-slate-400">events / exams only</span>}
                    </td>
                    <td className="text-right tabular-nums text-slate-600">{u.avg_fill === null ? "–" : `${Math.round(u.avg_fill * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
