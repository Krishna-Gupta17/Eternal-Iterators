import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Database, ListOrdered } from "lucide-react";
import { api } from "../api";
import { useApp } from "../App";
import { Alert, Button, Card, useToast } from "../components/ui";

export default function Settings() {
  const { meta, refresh } = useApp();
  const toast = useToast();
  const [order, setOrder] = useState(meta.priority_order);
  const [busy, setBusy] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => setOrder(meta.priority_order), [meta.priority_order]);

  const move = (i: number, d: number) => {
    const o = [...order];
    [o[i], o[i + d]] = [o[i + d], o[i]];
    setOrder(o);
  };
  const save = async () => {
    setBusy("prio");
    try { await api("/api/admin/priority", { method: "PUT", body: { order } }); toast("success", "Priority order saved"); await refresh(); }
    catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };
  const load = async () => {
    if (!file) return;
    if (!confirm("Loading a dataset replaces all current data. Continue?")) return;
    setBusy("data");
    const fd = new FormData();
    fd.append("file", file);
    try { const r = await api("/api/admin/dataset", { form: fd }); toast("success", `Loaded ${r.dataset_id}. Generate the timetable next.`); await refresh(); }
    catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title={<span className="flex items-center gap-2"><ListOrdered className="h-4 w-4" /> Priority order</span>}
        subtitle="Decides who gets a room when two requests want it at the same time. Applies to new decisions.">
        <ol className="space-y-2">
          {order.map((c, i) => (
            <li key={c} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-50 text-xs font-bold text-indigo-700">{i + 1}</span>
              <span className="flex-1 text-sm font-medium">{meta.priority_labels[c] || c}</span>
              <button disabled={i === 0} onClick={() => move(i, -1)} className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30" aria-label="Move up"><ArrowUp className="h-4 w-4" /></button>
              <button disabled={i === order.length - 1} onClick={() => move(i, 1)} className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30" aria-label="Move down"><ArrowDown className="h-4 w-4" /></button>
            </li>))}
        </ol>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setOrder(meta.priority_order)}>Undo</Button>
          <Button onClick={save} loading={busy === "prio"} disabled={order.join() === meta.priority_order.join()}>Save order</Button>
        </div>
      </Card>
      <Card title={<span className="flex items-center gap-2"><Database className="h-4 w-4" /> Load dataset</span>} subtitle="A cit_public.json-format file. Replaces the current data; then generate the timetable.">
        <input type="file" accept=".json,application/json" className="input" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <div className="mt-3"><Alert tone="info">Change files (faculty leave, room closures, new sessions) are loaded in <b>Changes & approvals</b>.</Alert></div>
        <div className="mt-4 flex justify-end"><Button onClick={load} loading={busy === "data"} disabled={!file}>Load dataset</Button></div>
      </Card>
    </div>
  );
}
