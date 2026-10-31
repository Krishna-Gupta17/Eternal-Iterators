import { useState } from "react";
import { DAYS, PERIOD_TIME } from "../api";
import { cx, Tabs } from "./ui";

export type GridItem = {
  key: string;
  day: string;
  start: number;
  end: number;
  title: string;
  lines: string[];
  kind: "lecture" | "tutorial" | "lab" | "booking" | "override";
  onClick?: () => void;
};

export const KIND_STYLE: Record<GridItem["kind"], string> = {
  lecture: "border-sky-300 bg-sky-50 text-sky-950 hover:bg-sky-100",
  tutorial: "border-violet-300 bg-violet-50 text-violet-950 hover:bg-violet-100",
  lab: "border-amber-300 bg-amber-50 text-amber-950 hover:bg-amber-100",
  booking: "border-emerald-300 bg-emerald-50 text-emerald-950 hover:bg-emerald-100",
  override: "border-dashed border-rose-300 bg-rose-50 text-rose-950 hover:bg-rose-100",
};

const col = (p: number) => (p <= 5 ? p + 1 : p + 2); // column 1 = day label, column 7 = lunch

export default function WeekGrid({ items, showEvening = true }: { items: GridItem[]; showEvening?: boolean }) {
  const last = showEvening ? 13 : 9;
  const periods = Array.from({ length: last }, (_, i) => i + 1);
  const template = `64px repeat(5, minmax(92px, 1fr)) 36px repeat(4, minmax(92px, 1fr))${showEvening ? " repeat(4, minmax(92px, 1fr))" : ""}`;
  const [day, setDay] = useState(DAYS[0]);
  return (
    <>
      {/* desktop / tablet grid */}
      <div className="hidden overflow-x-auto rounded-lg border border-slate-200 md:block">
        <div className="min-w-[1000px]">
          <div className="grid border-b border-slate-200 bg-slate-50 text-xs" style={{ gridTemplateColumns: template }}>
            <div />
            {periods.map((p) => (
              <div key={p} className={cx("px-2 py-2 text-center", p >= 10 && "bg-indigo-50/70")} style={{ gridColumn: col(p) }}>
                <p className="font-semibold text-slate-700">P{p}</p>
                <p className="text-slate-500">{PERIOD_TIME[p]}</p>
              </div>
            ))}
            <div className="flex items-center justify-center bg-slate-100" style={{ gridColumn: 7, gridRow: 1 }}>
              <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 [writing-mode:vertical-rl]">Lunch</span>
            </div>
          </div>
          {DAYS.map((d) => (
            <div key={d} className="relative border-b border-slate-100 last:border-b-0">
              <div className="absolute inset-0 grid" style={{ gridTemplateColumns: template }}>
                <div className="flex items-center justify-center border-r border-slate-100 bg-slate-50 text-xs font-semibold text-slate-600" style={{ gridColumn: 1 }}>{d}</div>
                <div className="bg-slate-100/80" style={{ gridColumn: 7 }} />
                {showEvening && <div className="bg-indigo-50/40" style={{ gridColumn: `${col(10)} / ${col(13) + 1}` }} />}
              </div>
              <div className="relative grid" style={{ gridTemplateColumns: template, gridAutoRows: "minmax(60px, auto)", gridAutoFlow: "row dense" }}>
                <div style={{ gridColumn: 1, gridRow: 1 }} />
                {items.filter((it) => it.day === d && it.start <= last).sort((a, b) => a.start - b.start).map((it) => (
                  <button key={it.key} onClick={it.onClick} title={[it.title, ...it.lines].join("\n")}
                    className={cx("m-1 overflow-hidden rounded-md border px-2 py-1.5 text-left text-xs shadow-sm transition", KIND_STYLE[it.kind])}
                    style={{ gridColumn: `${col(it.start)} / ${col(Math.min(it.end, last)) + 1}` }}>
                    <p className="truncate font-semibold">{it.title}</p>
                    {it.lines.map((l, i) => <p key={i} className="truncate opacity-75">{l}</p>)}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
      {/* phone: one day at a time */}
      <div className="space-y-3 md:hidden">
        <Tabs value={day} onChange={setDay} items={DAYS.map((d) => ({ id: d, label: d }))} />
        <div className="space-y-2">
          {items.filter((it) => it.day === day).sort((a, b) => a.start - b.start).map((it, i, arr) => (
            <div key={it.key}>
              {it.start >= 6 && (i === 0 || arr[i - 1].start < 6) && <p className="my-2 text-center text-[11px] font-semibold uppercase tracking-widest text-slate-400">Lunch 13:00–14:00</p>}
              <button onClick={it.onClick} className={cx("flex w-full gap-3 rounded-lg border p-3 text-left text-sm", KIND_STYLE[it.kind])}>
                <div className="w-14 shrink-0 text-xs"><p className="font-semibold">P{it.start}{it.end > it.start ? `–${it.end}` : ""}</p><p className="opacity-70">{PERIOD_TIME[it.start]}</p></div>
                <div className="min-w-0"><p className="font-semibold">{it.title}</p>{it.lines.map((l, j) => <p key={j} className="truncate text-xs opacity-75">{l}</p>)}</div>
              </button>
            </div>
          ))}
          {items.filter((it) => it.day === day).length === 0 && <p className="py-6 text-center text-sm text-slate-500">Nothing scheduled on {day}.</p>}
        </div>
      </div>
    </>
  );
}

export function Legend() {
  const l: [GridItem["kind"], string][] = [["lecture", "Lecture"], ["tutorial", "Tutorial"], ["lab", "3-hour lab"], ["booking", "Spectra booking"], ["override", "Week-7 moved class"]];
  return (
    <div className="flex flex-wrap gap-2 text-xs">
      {l.map(([k, t]) => <span key={k} className={cx("rounded border px-2 py-0.5", KIND_STYLE[k])}>{t}</span>)}
      <span className="rounded border border-indigo-200 bg-indigo-50/60 px-2 py-0.5 text-indigo-700">P10–P13 events only</span>
    </div>
  );
}
