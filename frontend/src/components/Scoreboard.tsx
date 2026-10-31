import { CheckCircle2, ShieldAlert } from "lucide-react";
import { Scoreboard as SB } from "../types";
import { cx } from "./ui";

const SOFT_NAMES: Record<string, string> = {
  S1: "Faculty avoid-times", S2: "Student free periods", S3: "Over 6h/day", S4: "Same course twice a day",
  S5: "Room under 40% full", S6: "Classes at P9 (17:00)",
};

export default function Scoreboard({ sb }: { sb: SB }) {
  const tiles = [
    { label: "Hard violations", value: sb.hard_violations, good: sb.hard_violations === 0, note: sb.hard_violations === 0 ? "Official validator" : Object.entries(sb.hard_by_rule).map(([k, v]) => `${k}×${v}`).join(" ") },
    { label: "Soft penalty", value: sb.soft_total, good: true, note: "Lower is better" },
    { label: "Booking score", value: sb.booking_score ?? "–", good: true, note: sb.booking_score_max ? `of ${sb.booking_score_max} if every request were approved` : "" },
    { label: "Sessions moved", value: sb.sessions_moved ?? 0, good: true, note: "since generation" },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((t, i) => (
          <div key={t.label} className={cx("rounded-xl border p-4", i === 0 ? (t.good ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50") : "border-slate-200 bg-white")}>
            <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
              {i === 0 && (t.good ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : <ShieldAlert className="h-3.5 w-3.5 text-rose-600" />)}
              {t.label}
            </p>
            <p className={cx("mt-1 text-3xl font-bold tabular-nums", i === 0 ? (t.good ? "text-emerald-700" : "text-rose-700") : "text-slate-900")}>{t.value}</p>
            <p className="mt-0.5 truncate text-xs text-slate-500">{t.note}</p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        {Object.entries(sb.soft_penalties).map(([k, v]) => (
          <span key={k} className={cx("rounded-full px-2.5 py-1 ring-1 ring-inset", v ? "bg-amber-50 text-amber-800 ring-amber-200" : "bg-slate-50 text-slate-500 ring-slate-200")}>
            {k} {SOFT_NAMES[k]}: <b>{v}</b>
          </span>
        ))}
        {sb.exam_soft_penalty !== null && (
          <span className="rounded-full bg-slate-50 px-2.5 py-1 text-slate-500 ring-1 ring-inset ring-slate-200">Exams: two on one day <b>{sb.exam_soft_penalty}</b></span>
        )}
        <span className="rounded-full bg-slate-50 px-2.5 py-1 text-slate-500 ring-1 ring-inset ring-slate-200">Priority inversions <b>{sb.priority_inversions.length}</b></span>
      </div>
    </div>
  );
}
