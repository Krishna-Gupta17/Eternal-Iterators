import { useEffect, useState } from "react";
import { CheckCircle2, Database, FileJson, FileSpreadsheet, FileUp, RotateCcw, X } from "lucide-react";
import { api } from "../api";
import { useApp } from "../App";
import { Alert, Badge, Button, Card, cx, Tabs, useToast } from "./ui";

type Format = { core: string[]; core_optional: string[]; overlays: string[]; required_columns: Record<string, string[]> };
const entityOf = (name: string) => name.toLowerCase().replace(/^.*[\\/]/, "").replace(/\.csv$/, "");

/** Admin: replace the active dataset with a starter pack (one cit_public.json, or CSV files), or reset to the demo data. */
export default function DatasetUpload() {
  const { meta, job, trackJob, refresh, go } = useApp();
  const toast = useToast();
  const [mode, setMode] = useState<"json" | "csv">("json");
  const [files, setFiles] = useState<File[]>([]);
  const [fmt, setFmt] = useState<Format | null>(null);
  const [report, setReport] = useState<any>(null);
  const [clear, setClear] = useState(true);
  const [regen, setRegen] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const running = job?.status === "running";
  useEffect(() => { api<Format>("/api/admin/dataset/format").then(setFmt).catch(() => setFmt(null)); }, []);
  useEffect(() => { setFiles([]); setReport(null); }, [mode]);

  const add = (list: FileList | null) => {
    if (!list) return;
    const picked = Array.from(list);
    if (mode === "json") setFiles(picked.filter((f) => f.name.toLowerCase().endsWith(".json")).slice(0, 1));
    else setFiles((cur) => [...cur.filter((c) => !picked.some((p) => entityOf(p.name) === entityOf(c.name))), ...picked.filter((f) => f.name.toLowerCase().endsWith(".csv"))]);
    setReport(null);
  };
  const have = new Set(files.map((f) => entityOf(f.name)));
  const known = new Set([...(fmt?.core ?? []), ...(fmt?.core_optional ?? []), ...(fmt?.overlays ?? [])]);
  const allCore = (fmt?.core ?? []).every((c) => have.has(c));

  const send = async (dry: boolean) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("dry_run", String(dry));
    fd.append("clear_missing", String(mode === "csv" && clear));
    fd.append("generate_now", String(regen));
    return api("/api/admin/dataset", { form: fd });
  };
  const validate = async () => {
    setBusy("validate");
    try { setReport(await send(true)); } catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };
  const apply = async () => {
    if (!confirm("Replace the active dataset? The timetable, bookings, pending changes and approvals are cleared and rebuilt from the new data.")) return;
    setBusy("apply");
    try {
      const r = await send(false);
      toast("success", `Loaded ${r.dataset_id} (${r.counts.sessions} sessions, ${r.counts.rooms} rooms)${r.job ? "; generating the timetable…" : ""}`);
      if (r.job) trackJob(r.job);
      setFiles([]); setReport(null);
      await refresh();          // reloads rooms/faculty/groups/courses everywhere in the UI
      go("overview");
    } catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };
  const reset = async () => {
    if (!confirm("Reset all data to the built-in demo dataset (cit_public.json)? Timetable, bookings and approvals will be cleared.")) return;
    setBusy("reset");
    try {
      await api("/api/admin/reset", { method: "POST" });
      toast("success", "Demo data restored. Generate the timetable to start.");
      setFiles([]); setReport(null);
      await refresh();
    } catch (e: any) { toast("error", e.message); }
    setBusy(null);
  };

  const fileRow = (name: string, label: string, required?: boolean) => (
    <li key={name} className="flex items-center justify-between gap-2 py-1 text-xs">
      <span className="font-mono">{name}.csv{required && <span className="text-rose-600">*</span>}</span>
      {have.has(name) ? <Badge tone="green"><CheckCircle2 className="h-3 w-3" /> added</Badge> : <Badge tone={required ? "amber" : "slate"}>{label}</Badge>}
    </li>
  );

  return (
    <Card title={<span className="flex items-center gap-2"><Database className="h-4 w-4" /> Dataset: upload a starter pack or reset</span>}
      subtitle={`Active: ${meta.dataset_id} · ${meta.rooms.length} rooms · ${meta.faculty.length} faculty · ${meta.groups.length} groups · ${meta.courses.length} courses`}
      actions={<Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} onClick={reset} loading={busy === "reset"} disabled={running}>Reset to demo data</Button>}>
      <Tabs value={mode} onChange={setMode} items={[{ id: "json", label: <span className="flex items-center gap-1.5"><FileJson className="h-4 w-4" /> Unified JSON</span> },
        { id: "csv", label: <span className="flex items-center gap-1.5"><FileSpreadsheet className="h-4 w-4" /> CSV files</span> }]} />
      <div className="mt-4 grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <div className="space-y-3">
          <label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
            className={cx("flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition",
              drag ? "border-indigo-500 bg-indigo-50" : "border-slate-300 hover:border-indigo-400")}>
            <FileUp className="h-8 w-8 text-slate-400" />
            <span className="text-sm font-medium">{mode === "json" ? "Drop cit_public.json here or click to choose" : "Drop one or more starter-pack CSV files here or click to choose"}</span>
            <span className="text-xs text-slate-500">{mode === "json" ? "One file with calendar, rooms, faculty, student_groups, courses, sessions, booking_requests…" : "Uploaded entities replace the same entities of the dataset as loaded; the rest are kept."}</span>
            <input type="file" multiple={mode === "csv"} accept={mode === "json" ? ".json,application/json" : ".csv,text/csv"} className="hidden"
              onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          </label>
          {files.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">{files.map((f) => (
              <li key={f.name}><Badge tone={mode === "csv" && !known.has(entityOf(f.name)) ? "red" : "indigo"}>{f.name}
                <button aria-label={`Remove ${f.name}`} onClick={() => { setFiles(files.filter((x) => x !== f)); setReport(null); }}><X className="h-3 w-3" /></button></Badge></li>))}</ul>)}
          {mode === "csv" && (
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-0.5 accent-indigo-600" checked={clear} onChange={(e) => { setClear(e.target.checked); setReport(null); }} />
              <span>Clear exams and overlays I didn't upload <span className="block text-xs text-slate-500">Recommended for a new institute: old booking requests and closures would point at rooms and groups that no longer exist.{allCore ? "" : " (Usually off when you only update one or two files.)"}</span></span></label>)}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-indigo-600" checked={regen} onChange={(e) => setRegen(e.target.checked)} /> Generate a clean timetable right after loading</label>
        </div>
        {mode === "csv" && fmt && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
            <div className="rounded-lg border border-slate-200 p-3">
              <p className="label">Core master entities (needed to generate)</p>
              <ul>{fmt.core.map((c) => fileRow(c, "keeps current", true))}{fmt.core_optional.map((c) => fileRow(c, "if scheduling exams"))}</ul>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <p className="label">Operational / constraint overlays (optional)</p>
              <ul>{fmt.overlays.map((c) => fileRow(c, "optional"))}</ul>
            </div>
          </div>
        )}
        {mode === "json" && <Alert tone="info">The unified file replaces everything at once. Change files (faculty leave, room closures, new sessions) go in <b>Changes & approvals</b> instead.</Alert>}
      </div>
      {report && (
        <div className="mt-4">
          {report.ok ? (
            <Alert tone="success" title={`Valid: ${report.dataset_id} · ${report.counts.sessions} sessions, ${report.counts.rooms} rooms, ${report.counts.faculty} faculty, ${report.counts.student_groups} groups`}>
              <p>Replaces: {report.replaced.join(", ") || "nothing"}{report.cleared?.length ? ` · clears: ${report.cleared.join(", ")}` : ""}{report.kept?.length ? ` · keeps: ${report.kept.join(", ")}` : ""}</p>
              {report.warnings?.length > 0 && <ul className="mt-1 list-inside list-disc text-xs">{report.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
            </Alert>
          ) : (
            <Alert tone="error" title="The files can't be loaded">
              <ul className="list-inside list-disc">{report.errors.map((e: string) => <li key={e}>{e}</li>)}</ul>
            </Alert>
          )}
        </div>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant={report?.ok ? "secondary" : "primary"} onClick={validate} loading={busy === "validate"} disabled={!files.length || running}>Validate files</Button>
        {report?.ok && <Button onClick={apply} loading={busy === "apply"} disabled={running}>Replace dataset{regen ? " & regenerate" : ""}</Button>}
      </div>
    </Card>
  );
}
