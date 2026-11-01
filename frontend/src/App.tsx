import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { BookOpen, CalendarDays, GitPullRequestArrow, HelpCircle, LayoutDashboard, Loader2, LogOut, Menu,
  RotateCcw, Settings as SettingsIcon, Sparkles, Ticket, Warehouse, X } from "lucide-react";
import { api, getToken, setToken } from "./api";
import { Job, Meta, Summary, User } from "./types";
import { Button, cx, ToastProvider, useToast } from "./components/ui";
import ExplainModal, { ExplainTarget } from "./components/ExplainModal";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Timetable from "./pages/Timetable";
import Rooms from "./pages/Rooms";
import Bookings from "./pages/Bookings";
import Changes from "./pages/Changes";
import Assistant from "./pages/Assistant";
import Settings from "./pages/Settings";
import Ask from "./pages/Ask";

interface Ctx {
  user: User;
  meta: Meta;
  summary: Summary | null;
  refresh: () => Promise<void>;
  job: Job | null;
  trackJob: (j: Job) => void;
  explain: (t: ExplainTarget) => void;
  go: (page: string) => void;
  version: number;
}
const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

const PAGES = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "timetable", label: "Timetable", icon: CalendarDays, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "rooms", label: "Rooms", icon: Warehouse, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "bookings", label: "Venue bookings", icon: Ticket, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "assistant", label: "Faculty assistant", icon: Sparkles, roles: ["department_coordinator", "faculty", "admin"] },
  { id: "changes", label: "Changes & approvals", icon: GitPullRequestArrow, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "ask", label: "Ask why", icon: HelpCircle, roles: ["admin", "department_coordinator", "faculty", "club"] },
  { id: "settings", label: "Admin settings", icon: SettingsIcon, roles: ["admin"] },
];

function pageFromHash() {
  return window.location.hash.replace("#/", "") || "overview";
}

export default function App() {
  return (
    <ToastProvider>
      <Root />
    </ToastProvider>
  );
}

function Root() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(!!getToken());
  useEffect(() => {
    if (!getToken()) return;
    api<User>("/api/auth/me").then(setUser).catch(() => setToken(null)).finally(() => setChecking(false));
  }, []);
  if (checking) return <FullPageSpinner />;
  if (!user) return <Login onLogin={(u) => { setUser(u); window.location.hash = "#/overview"; }} />;
  return <Shell user={user} onLogout={() => { setToken(null); setUser(null); }} />;
}

function FullPageSpinner() {
  return <div className="flex min-h-screen items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-indigo-600" /></div>;
}

function Shell({ user, onLogout }: { user: User; onLogout: () => void }) {
  const toast = useToast();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState(pageFromHash());
  const [menu, setMenu] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [explainT, setExplainT] = useState<ExplainTarget | null>(null);
  const [version, setVersion] = useState(0);
  const [resetting, setResetting] = useState(false);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api<Meta>("/api/meta"), api<Summary>("/api/summary")]);
      setMeta(m);
      setSummary(s);
      setVersion((v) => v + 1);
      if (s.job.status === "running") setJob(s.job);
    } catch (e: any) {
      toast("error", e.message);
      if (e.status === 401) onLogout();
    }
  }, [toast, onLogout]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const h = () => { setPage(pageFromHash()); setMenu(false); };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  // poll a running solver job, then refresh everything when it ends
  useEffect(() => {
    if (!job || job.status !== "running") return;
    pollRef.current = window.setInterval(async () => {
      try {
        const j = await api<Job>("/api/jobs/current");
        setJob(j);
        if (j.status !== "running") {
          if (j.status === "done") toast("success", j.kind === "generate"
            ? `Timetable generated: ${j.result.hard} hard violations, soft penalty ${j.result.soft}, booking score ${j.result.booking_score}`
            : j.result?.status === "infeasible" ? "No valid fix exists: see the conflicting constraints" : `Proposal ready: ${j.result?.moved ?? 0} sessions would move. Review and approve it.`);
          else toast("error", j.error || "Job failed");
          refresh();
        }
      } catch { /* transient network error: keep polling */ }
    }, 1500);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [job?.status, job?.id, toast, refresh]);

  const go = (p: string) => { window.location.hash = `#/${p}`; };

  if (!meta) return <FullPageSpinner />;
  const pages = PAGES.filter((p) => p.roles.includes(user.role));
  const current = pages.find((p) => p.id === page) ? page : "overview";

  const reset = async () => {
    if (!confirm("Reset all demo data to the clean starter dataset? Timetable, bookings and approvals will be cleared.")) return;
    setResetting(true);
    try {
      await api("/api/admin/reset", { method: "POST" });
      toast("success", "Demo data reset. Generate the timetable to start.");
      await refresh();
      go("overview");
    } catch (e: any) { toast("error", e.message); }
    setResetting(false);
  };

  const ctx: Ctx = { user, meta, summary, refresh, job, trackJob: setJob, explain: setExplainT, go, version };
  const nav = (
    <nav className="space-y-1">
      {pages.map((p) => (
        <a key={p.id} href={`#/${p.id}`}
          className={cx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
            current === p.id ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900")}>
          <p.icon className="h-4 w-4" />
          <span className="flex-1">{p.label}</span>
          {p.id === "changes" && summary && summary.pending_changes + summary.pending_bookings > 0 && (
            <span className="rounded-full bg-indigo-600 px-1.5 text-xs text-white">{summary.pending_changes + summary.pending_bookings}</span>)}
        </a>
      ))}
    </nav>
  );

  return (
    <AppCtx.Provider value={ctx}>
      <div className="min-h-screen lg:pl-60">
        <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r border-slate-200 bg-white p-4 lg:flex">
          <Brand />
          <div className="mt-6 flex-1">{nav}</div>
          <UserBox user={user} onLogout={onLogout} />
        </aside>
        {menu && (
          <div className="fixed inset-0 z-40 bg-slate-900/30 lg:hidden" onClick={() => setMenu(false)}>
            <aside className="flex h-full w-72 flex-col bg-white p-4" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between"><Brand /><button onClick={() => setMenu(false)} aria-label="Close menu"><X className="h-5 w-5" /></button></div>
              <div className="mt-6 flex-1">{nav}</div>
              <UserBox user={user} onLogout={onLogout} />
            </aside>
          </div>
        )}
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur sm:px-6">
          <button className="lg:hidden" onClick={() => setMenu(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-900">{pages.find((p) => p.id === current)?.label}</p>
            <p className="hidden truncate text-xs text-slate-500 sm:block">{meta.institute} · {meta.dataset_id} · {user.role_label}</p>
          </div>
          {job?.status === "running" && (
            <span className="hidden items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 sm:inline-flex">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> {job.kind === "generate" ? "Generating timetable" : "Solving reschedule"} · {Math.round(job.elapsed || 0)}s
            </span>
          )}
          {user.role === "admin" && (
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} onClick={reset} loading={resetting}
              disabled={job?.status === "running"} title="Restore the clean starter dataset">
              <span className="hidden sm:inline">Reset demo data</span>
            </Button>
          )}
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6 sm:py-6">
          {current === "overview" && <Dashboard />}
          {current === "timetable" && <Timetable />}
          {current === "rooms" && <Rooms />}
          {current === "bookings" && <Bookings />}
          {current === "assistant" && <Assistant />}
          {current === "changes" && <Changes />}
          {current === "ask" && <Ask />}
          {current === "settings" && <Settings />}
        </main>
      </div>
      <ExplainModal target={explainT} onClose={() => setExplainT(null)} />
    </AppCtx.Provider>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white"><BookOpen className="h-5 w-5" /></div>
      <div>
        <p className="text-sm font-bold leading-tight text-slate-900">CIT Scheduler</p>
        <p className="text-xs leading-tight text-slate-500">Timetable · Venues · Spectra</p>
      </div>
    </div>
  );
}

function UserBox({ user, onLogout }: { user: User; onLogout: () => void }) {
  return (
    <div className="mt-4 rounded-lg border border-slate-200 p-3">
      <p className="truncate text-sm font-medium text-slate-900">{user.name}</p>
      <p className="truncate text-xs text-slate-500">{user.role_label} · {user.username}</p>
      <button onClick={onLogout} className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-rose-600">
        <LogOut className="h-3.5 w-3.5" /> Log out / switch role
      </button>
    </div>
  );
}
