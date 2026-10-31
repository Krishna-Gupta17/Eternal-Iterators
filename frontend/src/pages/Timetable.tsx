import { Fragment, useEffect, useMemo, useState } from "react";
import { CalendarX2, PartyPopper } from "lucide-react";
import { api } from "../api";
import { useApp } from "../App";
import { PlacedBooking, SessionEntry } from "../types";
import WeekGrid, { GridItem, Legend } from "../components/WeekGrid";
import { Badge, Card, cx, Empty, Skeleton, Tabs } from "../components/ui";

type View = "group" | "faculty" | "room" | "exams";

export default function Timetable() {
  const { user, meta, summary, explain, version } = useApp();
  const [view, setView] = useState<View>(user.role === "faculty" ? "faculty" : "group");
  const [week7, setWeek7] = useState(false);
  const [data, setData] = useState<{ sessions: SessionEntry[]; bookings: PlacedBooking[]; overrides: any[] } | null>(null);
  const [sel, setSel] = useState<Record<string, string>>({
    group: user.department && meta.groups.find((g) => g.id === `${user.department}-2`) ? `${user.department}-2` : meta.groups[0]?.id,
    faculty: user.faculty_id || meta.faculty[0]?.id,
    room: meta.rooms[0]?.id,
  });

  useEffect(() => {
    setData(null);
    api(`/api/timetable?week7=${week7}`).then(setData).catch(() => setData({ sessions: [], bookings: [], overrides: [] }));
  }, [week7, version]);

  const items: GridItem[] = useMemo(() => {
    if (!data || view === "exams") return [];
    const id = sel[view];
    const moved = new Set(data.overrides.map((o) => o.session_id));
    const ss = data.sessions.filter((s) => (view === "group" ? s.groups.includes(id) : view === "faculty" ? s.faculty_id === id : s.room_id === id));
    const out: GridItem[] = ss.map((s) => ({
      key: s.id, day: s.day, start: s.start_period, end: s.start_period + s.duration - 1,
      title: `${s.course_id} ${s.kind === "lab" ? "Lab" : s.kind === "tutorial" ? "Tut" : ""}`.trim(),
      lines: [view === "room" ? s.groups.join(", ") : s.room_id, view === "faculty" ? s.groups.join(", ") : s.faculty_name,
        ...(s.basket_slot ? [`Elective ${s.basket_slot}`] : [])].slice(0, 2),
      kind: moved.has(s.id) ? "override" : s.kind, onClick: () => explain({ kind: "session", id: s.id }),
    }));
    if (week7) {
      for (const b of data.bookings) {
        const show = view === "room" ? b.room_id === id : view === "group" ? b.mandatory_for_groups.includes(id) : false;
        if (show) out.push({ key: b.request_id, day: b.day, start: b.start_period, end: b.end_period, title: b.title,
          lines: [`${b.request_id} · ${b.room_id}`], kind: "booking", onClick: () => explain({ kind: "booking", id: b.request_id }) });
      }
    }
    return out;
  }, [data, view, sel, week7, explain]);

  if (summary && !summary.has_timetable) {
    return <Card><Empty icon={<CalendarX2 className="h-10 w-10" />} title="No timetable yet">An Admin has to generate it from the Overview page.</Empty></Card>;
  }

  const options = view === "group" ? meta.groups.map((g) => ({ id: g.id, label: `${g.id} · ${g.name}` }))
    : view === "faculty" ? meta.faculty.map((f) => ({ id: f.id, label: `${f.id} · ${f.name} (${f.department})` }))
      : meta.rooms.map((r) => ({ id: r.id, label: `${r.id} · ${r.name} (${r.capacity}, ${r.type.replace("_", " ")})` }));
  const hours = items.filter((i) => i.kind !== "booking").reduce((n, i) => n + i.end - i.start + 1, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <Tabs value={view} onChange={setView} items={[
          { id: "group", label: "By student group" }, { id: "faculty", label: "By faculty" },
          { id: "room", label: "By room" }, { id: "exams", label: "Midsem exams (Week 8)" }]} />
        {view !== "exams" && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <select className="input sm:w-80" value={sel[view]} onChange={(e) => setSel({ ...sel, [view]: e.target.value })}>
              {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            <label className={cx("inline-flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-lg border px-3 py-2 text-sm",
              week7 ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-slate-300 bg-white text-slate-700")}>
              <input type="checkbox" className="accent-emerald-600" checked={week7} onChange={(e) => setWeek7(e.target.checked)} />
              <PartyPopper className="h-4 w-4" /> Spectra week view
            </label>
          </div>
        )}
      </div>
      {view === "exams" ? <ExamView /> : (
        <Card title={<span className="flex flex-wrap items-center gap-2">{options.find((o) => o.id === sel[view])?.label}<Badge>{hours} h/week</Badge></span>}
          subtitle="Click any card for “Why is this here?”" actions={<Legend />}>
          {!data ? <Skeleton className="h-96" /> : items.length === 0 ? <Empty title="Nothing scheduled here" /> : <WeekGrid items={items} />}
        </Card>
      )}
    </div>
  );
}

function ExamView() {
  const { meta, explain, version } = useApp();
  const [res, setRes] = useState<any>(null);
  const [group, setGroup] = useState("");
  useEffect(() => { api("/api/exams").then(setRes).catch(() => setRes({ exams: [] })); }, [version]);
  if (!res) return <Skeleton className="h-96" />;
  const days: string[] = res.calendar?.days || [];
  const sessions: { id: string; start: string; end: string }[] = res.calendar?.sessions || [];
  const exams = res.exams.filter((e: any) => !group || e.groups.includes(group));
  return (
    <Card title="Midsem exams · Week 8" subtitle="Rooms seat half their capacity during exams; an exam may use several rooms"
      actions={<select className="input w-56" value={group} onChange={(e) => setGroup(e.target.value)}>
        <option value="">All groups</option>{meta.groups.map((g) => <option key={g.id} value={g.id}>{g.id}</option>)}</select>}>
      {res.exams.length === 0 ? <Empty title="No exam schedule yet" /> : (
        <div className="overflow-x-auto">
          <div className="grid min-w-[760px] gap-2" style={{ gridTemplateColumns: `90px repeat(${days.length}, minmax(0, 1fr))` }}>
            <div />
            {days.map((d) => <div key={d} className="rounded-md bg-slate-50 py-2 text-center text-xs font-semibold text-slate-600">{d}</div>)}
            {sessions.map((s) => (
              <Fragment key={s.id}>
                <div className="flex flex-col justify-center rounded-md bg-slate-50 px-2 text-xs"><b>{s.id}</b><span className="text-slate-500">{s.start}–{s.end}</span></div>
                {days.map((d) => (
                  <div key={d + s.id} className="min-h-[90px] space-y-1.5 rounded-md border border-slate-100 p-1.5">
                    {exams.filter((e: any) => e.day === d && e.session === s.id).map((e: any) => (
                      <button key={e.exam_id} onClick={() => explain({ kind: "exam", id: e.exam_id })}
                        className="w-full rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-left text-xs text-rose-950 hover:bg-rose-100">
                        <p className="font-semibold">{e.exam_id.replace("EX-", "")}</p>
                        <p className="truncate opacity-75">{e.groups.length > 2 ? `${e.groups.length} groups` : e.groups.join(", ")} · {e.size}</p>
                        <p className="truncate opacity-75">{e.rooms.join(", ")} ({e.seats} seats)</p>
                      </button>
                    ))}
                  </div>
                ))}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
