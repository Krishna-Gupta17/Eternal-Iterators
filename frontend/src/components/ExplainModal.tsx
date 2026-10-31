import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { api } from "../api";
import { Badge, Modal, Skeleton } from "./ui";

export type ExplainTarget = { kind: "session" | "booking" | "exam"; id: string };

export default function ExplainModal({ target, onClose }: { target: ExplainTarget | null; onClose: () => void }) {
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!target) return;
    setRes(null);
    setErr(null);
    api(`/api/explain/${target.kind}/${encodeURIComponent(target.id)}`).then(setRes).catch((e) => setErr(e.message));
  }, [target]);
  return (
    <Modal open={!!target} onClose={onClose} title={<span className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-indigo-600" /> Why is this here? · {target?.id}</span>} wide>
      {err && <p className="text-sm text-rose-600">{err}</p>}
      {!res && !err && <div className="space-y-2"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-5/6" /><Skeleton className="h-4 w-2/3" /></div>}
      {res && <ExplainBody res={res} />}
    </Modal>
  );
}

export function ExplainBody({ res }: { res: any }) {
  const f = res.facts;
  return (
    <div className="space-y-4">
      <p className="leading-relaxed text-slate-700">{res.text}</p>
      <p className="text-xs text-slate-400">
        Facts computed by the scheduler's own code; {res.source === "llm" ? "worded by the LLM" : "worded by a deterministic template (no LLM)"}.
      </p>
      {f?.blocked && Object.keys(f.blocked).length > 0 && (
        <div>
          <p className="label">Other slots ruled out (hard constraints)</p>
          <div className="space-y-1.5">
            {Object.entries(f.blocked as Record<string, number>).map(([why, n]) => (
              <div key={why} className="flex items-start justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <span>{why}<span className="block text-xs text-slate-500">e.g. {(f.blocked_examples[why] || []).join(", ")}</span></span>
                <Badge tone="red">{n} slots</Badge>
              </div>
            ))}
          </div>
        </div>
      )}
      {f?.costlier_alternatives?.length > 0 && (
        <div>
          <p className="label">Legal but worse (soft penalty)</p>
          <div className="flex flex-wrap gap-1.5">{f.costlier_alternatives.map((x: string) => <Badge key={x} tone="amber">{x}</Badge>)}</div>
        </div>
      )}
      {f?.room_reason && <div><p className="label">Room choice</p><p className="text-sm text-slate-700">{f.room_reason}</p></div>}
      {f?.category_note && <div><p className="label">Priority</p><p className="text-sm text-slate-700">{f.category_note}</p></div>}
    </div>
  );
}
