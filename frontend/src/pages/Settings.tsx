import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ListOrdered } from "lucide-react";
import { api } from "../api";
import { useApp } from "../App";
import { Button, Card, useToast } from "../components/ui";
import DatasetUpload from "../components/DatasetUpload";

export default function Settings() {
  const { meta, refresh } = useApp();
  const toast = useToast();
  const [order, setOrder] = useState(meta.priority_order);
  const [busy, setBusy] = useState<string | null>(null);
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
      <div className="lg:col-span-2"><DatasetUpload /></div>
    </div>
  );
}
