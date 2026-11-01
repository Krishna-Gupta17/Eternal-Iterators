import { useEffect, useState } from "react";
import { ArrowLeft, GraduationCap, Landmark, Loader2, Users, Wrench } from "lucide-react";
import { api, setToken } from "../api";
import { User } from "../types";
import { Alert, Button, Field } from "../components/ui";
import citLogo from "../assets/cit_logo.png";

const DEMO_PASSWORD = "demo123";
const CARDS = [
  { username: "admin1", title: "Admin (Dean's office)", text: "Generate timetable, approve changes, Spectra decisions, reset demo data", icon: Landmark, color: "bg-indigo-600" },
  { username: "coord_cse", title: "Dept Coordinator (CSE)", text: "Department sessions, events, approve CSE lab bookings", icon: Wrench, color: "bg-sky-600" },
  { username: "f12", title: "Faculty (Dr. Amit Behera)", text: "Own timetable, AI assistant to move sessions or mark leave", icon: GraduationCap, color: "bg-emerald-600" },
  { username: "club_robotics", title: "Club Representative", text: "Book venues in English or Hinglish, track status and reasons", icon: Users, color: "bg-amber-500" },
];

export default function Login({ onLogin, onBackToLanding }: { onLogin: (u: User) => void; onBackToLanding?: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<User[]>([]);

  useEffect(() => { api<User[]>("/api/auth/demo-accounts").then(setAccounts).catch(() => {}); }, []);

  const doLogin = async (u: string, p: string) => {
    setBusy(u);
    setError(null);
    try {
      const r = await api<{ token: string; user: User }>("/api/auth/login", { body: { username: u, password: p } });
      setToken(r.token);
      onLogin(r.user);
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(null);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-50 via-white to-sky-50">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-10 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:py-16">
        <div>
          {onBackToLanding && (
            <button
              onClick={onBackToLanding}
              className="mb-8 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-indigo-600 transition"
            >
              <ArrowLeft className="h-4 w-4" /> Back to overview / landing page
            </button>
          )}
          <div className="flex items-center gap-3">
            <img src={citLogo} alt="CIT Logo" className="h-11 w-auto max-h-11 object-contain" />
            <div>
              <h1 className="text-xl font-bold text-slate-900">CIT Campus Scheduler</h1>
              <p className="text-sm text-slate-500">Chilika Institute of Technology</p>
            </div>
          </div>
          <h2 className="mt-10 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">Clash-free timetables, exams and Spectra week bookings.</h2>
          <p className="mt-4 max-w-xl text-slate-600">
            A CP-SAT solver builds the timetable, the official validator checks every change, and plain-language requests
            (English or Hinglish) become editable bookings. Pick a demo role to start.
          </p>
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            {CARDS.map((c) => (
              <button key={c.username} onClick={() => doLogin(c.username, DEMO_PASSWORD)} disabled={!!busy}
                className="group flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md disabled:opacity-60">
                <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-white ${c.color}`}>
                  {busy === c.username ? <Loader2 className="h-5 w-5 animate-spin" /> : <c.icon className="h-5 w-5" />}
                </div>
                <div>
                  <p className="font-semibold text-slate-900">{c.title}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{c.text}</p>
                  <p className="mt-2 text-xs font-medium text-indigo-600 group-hover:underline">1-click login as {c.username} →</p>
                </div>
              </button>
            ))}
          </div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <h3 className="text-lg font-semibold text-slate-900">Sign in</h3>
          <p className="mt-1 text-sm text-slate-500">All demo accounts use the password <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono">{DEMO_PASSWORD}</code></p>
          <form className="mt-6 space-y-4" onSubmit={(e) => { e.preventDefault(); doLogin(username, password); }}>
            <Field label="Username"><input className="input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin1" autoComplete="username" /></Field>
            <Field label="Password"><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
            {error && <Alert tone="error">{error}</Alert>}
            <Button className="w-full" loading={!!busy && busy === username}>Sign in</Button>
          </form>
          {accounts.length > 0 && (
            <div className="mt-8">
              <p className="label">All demo accounts</p>
              <div className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm">
                {accounts.map((a) => (
                  <button key={a.username} onClick={() => { setUsername(a.username); setPassword(DEMO_PASSWORD); }}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-slate-50">
                    <span className="font-mono text-xs text-slate-700">{a.username}</span>
                    <span className="truncate text-xs text-slate-500">{a.role_label}{a.department ? ` · ${a.department}` : ""}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
