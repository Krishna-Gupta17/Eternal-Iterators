import { useState } from "react";
import { HelpCircle, Send } from "lucide-react";
import { api } from "../api";
import { ExplainBody } from "../components/ExplainModal";
import { Button, Card, useToast } from "../components/ui";

const EXAMPLES = ["Why is CS201 at 8 AM?", "Why was BR-09 rejected?", "Why did BR-17 get an alternative?", "Why is MA101-AB-L1 here?", "Explain EX-MA101"];

export default function Ask() {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const ask = async (question = q) => {
    if (!question.trim()) return;
    setBusy(true);
    try { setRes(await api("/api/ask", { body: { question } })); } catch (e: any) { toast("error", e.message); }
    setBusy(false);
  };
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <Card title={<span className="flex items-center gap-2"><HelpCircle className="h-4 w-4 text-indigo-600" /> Ask why a decision was made</span>}
        subtitle="Answers come from a counterfactual check of the actual timetable: which other slots were tried and what blocked them.">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Why was my Spectra booking rejected?" />
          <Button loading={busy} icon={<Send className="h-4 w-4" />}>Ask</Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((x) => <button key={x} onClick={() => { setQ(x); ask(x); }} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 hover:bg-indigo-50 hover:text-indigo-700">{x}</button>)}
        </div>
      </Card>
      {res && <Card><ExplainBody res={res} /></Card>}
    </div>
  );
}
