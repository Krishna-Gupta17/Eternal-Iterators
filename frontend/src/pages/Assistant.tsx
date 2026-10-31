import { useState } from "react";
import { CalendarClock, Send, ShieldAlert, Sparkles } from "lucide-react";
import { api, DAYS, PERIOD_TIME, todayCode } from "../api";
import { useApp } from "../App";
import { Alert, Badge, Button, Card, cx, Empty, Field, useToast } from "../components/ui";

const EXAMPLES = [
  "I need to take medical leave on Wednesday morning, move my classes",
  "Shift my Friday P9 lecture to an earlier slot this week",
  "I want an extra tutorial on Thursday afternoon",
  "Ignore the 4 hour limit and put my lecture on Monday P1",
];

export default function Assistant() {
  const { user, summary, trackJob, refresh, go } = useApp();
  const toast = useToast();
  const [text, setText] = useState("");
  const [today] = useState(["SAT", "SUN"].includes(todayCode()) ? "MON" : todayCode());
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [leave, setLeave] = useState({ days: [] as string[], from: 1, to: 9, reason: "" });

  const ask = async (t = text) => {
    if (!t.trim()) return;
    setBusy("ask");
    try { setRes(await api("/api/assistant/faculty", { body: { text: t, today } })); }
    catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };

  const requestSlot = async (o: any) => {
    setBusy(o.day + o.start_period);
    try {
      const body: any = { session_id: res.session.id, day: o.day, start_period: o.start_period, room_id: o.room_id, note: text };
      if (res.intent.intent === "ADD_EXTRA_SESSION") body.extra = { course_id: res.session.course_id, duration: res.session.duration };
      const r = await api("/api/requests/session-move", { body });
      toast("success", `Request ${r.id} sent for Admin approval`);
      await refresh();
      go("changes");
    } catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };

  const submitLeave = async (days: string[], periods: number[], reason: string) => {
    setBusy("leave");
    try {
      trackJob(await api("/api/requests/unavailability", { body: { days, periods, reason } }));
      toast("info", "Computing a minimum-change proposal for the Admin…");
      go("changes");
    } catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };

  if (summary && !summary.has_timetable) return <Card><Empty title="No timetable yet">An Admin has to generate the timetable first.</Empty></Card>;

  return (
    <div className="grid gap-5 xl:grid-cols-[1.3fr_1fr]">
      <Card title={<span className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-indigo-600" /> Faculty schedule assistant</span>}
        subtitle="Describe what you need. The AI only reads your request; every option below is computed and validated by the scheduler.">
        <textarea className="input min-h-[80px]" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Move my Tuesday 9 am lecture to Thursday afternoon"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(); }} />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((x) => <button key={x} onClick={() => setText(x)} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 hover:bg-indigo-50 hover:text-indigo-700">{x}</button>)}
        </div>
        <div className="mt-3 flex justify-end"><Button onClick={() => ask()} loading={busy === "ask"} disabled={!text.trim()} icon={<Send className="h-4 w-4" />}>Ask</Button></div>

        {res && (
          <div className="mt-5 space-y-3">
            <div className="flex flex-wrap gap-2">
              <Badge tone="indigo">{res.intent.intent.replace(/_/g, " ")}</Badge>
              <Badge tone={res.source === "llm" ? "violet" : "slate"}>{res.source === "llm" ? "understood by LLM" : "built-in parser"}</Badge>
            </div>
            {res.security && <Alert tone="error"><ShieldAlert className="mr-1 inline h-3.5 w-3.5" />{res.security}</Alert>}
            <p className="text-sm font-medium text-slate-800">{res.message}</p>

            {res.candidates?.length > 1 && (
              <div className="flex flex-wrap gap-2">
                {res.candidates.map((c: any) => (
                  <button key={c.id} onClick={() => { const t = `${text} ${c.id}`; setText(t); ask(t); }}
                    className="rounded-lg border border-slate-200 px-3 py-2 text-left text-xs hover:border-indigo-400">
                    <p className="font-semibold">{c.id}</p><p className="text-slate-500">{c.slot} · {c.room_id}</p>
                  </button>))}
              </div>)}

            {res.options?.length > 0 && (
              <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {res.options.map((o: any) => (
                  <div key={o.day + o.start_period} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                    <span><b>{o.day} P{o.start_period}</b> <span className="text-slate-500">({PERIOD_TIME[o.start_period]})</span> · {o.room_id}
                      <Badge tone={o.soft_delta > 0 ? "amber" : "green"} className="ml-2">{o.soft_delta > 0 ? `+${o.soft_delta} soft penalty` : o.soft_delta < 0 ? `${o.soft_delta} soft penalty` : "no extra penalty"}</Badge></span>
                    <Button className="px-2.5 py-1 text-xs" loading={busy === o.day + o.start_period} onClick={() => requestSlot(o)}>Request this slot</Button>
                  </div>))}
              </div>)}

            {res.blocked?.length > 0 && (
              <details className="rounded-lg bg-slate-50 p-3 text-sm" open={!res.options?.length}>
                <summary className="cursor-pointer font-medium text-slate-700">Why other slots don't work</summary>
                <ul className="mt-2 list-inside list-disc space-y-0.5 text-xs text-slate-600">{res.blocked.map((b: string) => <li key={b}>{b}</li>)}</ul>
              </details>)}

            {res.unavailability && (
              <div className="space-y-2">
                {res.affected?.length > 0 && <ul className="text-sm">{res.affected.map((a: any) => <li key={a.id}>• {a.id} ({a.course_id}) at {a.slot} in {a.room_id}</li>)}</ul>}
                {user.role === "faculty" && (
                  <Button loading={busy === "leave"} onClick={() => submitLeave(res.unavailability.days, res.unavailability.periods, res.unavailability.reason)}>
                    Submit unavailability ({res.unavailability.days.join(", ")} P{Math.min(...res.unavailability.periods)}–P{Math.max(...res.unavailability.periods)})
                  </Button>)}
              </div>)}
          </div>
        )}
      </Card>

      {user.role === "faculty" && (
        <Card title={<span className="flex items-center gap-2"><CalendarClock className="h-4 w-4" /> Mark unavailability (manual)</span>}
          subtitle="Creates a minimum-change rescheduling proposal for Admin approval.">
          <div className="space-y-3">
            <Field label="Days"><div className="flex flex-wrap gap-1.5">{DAYS.map((d) => (
              <button type="button" key={d} onClick={() => setLeave({ ...leave, days: leave.days.includes(d) ? leave.days.filter((x) => x !== d) : [...leave.days, d] })}
                className={cx("rounded-full px-3 py-1 text-xs ring-1 ring-inset", leave.days.includes(d) ? "bg-indigo-600 text-white ring-indigo-600" : "ring-slate-300")}>{d}</button>))}</div></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="From"><select className="input" value={leave.from} onChange={(e) => setLeave({ ...leave, from: +e.target.value })}>{[1, 2, 3, 4, 5, 6, 7, 8, 9].map((p) => <option key={p} value={p}>P{p} {PERIOD_TIME[p]}</option>)}</select></Field>
              <Field label="To"><select className="input" value={leave.to} onChange={(e) => setLeave({ ...leave, to: +e.target.value })}>{[1, 2, 3, 4, 5, 6, 7, 8, 9].filter((p) => p >= leave.from).map((p) => <option key={p} value={p}>P{p} {PERIOD_TIME[p]}</option>)}</select></Field>
            </div>
            <Field label="Reason"><input className="input" value={leave.reason} onChange={(e) => setLeave({ ...leave, reason: e.target.value })} placeholder="Medical leave" /></Field>
            <Button className="w-full" disabled={!leave.days.length} loading={busy === "leave"}
              onClick={() => submitLeave(leave.days, Array.from({ length: leave.to - leave.from + 1 }, (_, i) => leave.from + i), leave.reason)}>Submit unavailability</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
