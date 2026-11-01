import type { ClassRequest } from "./types";

const TOKEN_KEY = "cit_token";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the session simply won't survive a reload */
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  } catch {
    throw new ApiError("Cannot reach the server. Is the backend running?", 0);
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = data?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : res.statusText;
    if (res.status === 401) setToken(null);
    throw new ApiError(msg, res.status);
  }
  return data as T;
}

export const DAYS = ["MON", "TUE", "WED", "THU", "FRI"];
export const PERIODS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13];
export const PERIOD_TIME: Record<number, string> = {
  1: "08:00", 2: "09:00", 3: "10:00", 4: "11:00", 5: "12:00", 6: "14:00", 7: "15:00",
  8: "16:00", 9: "17:00", 10: "18:00", 11: "19:00", 12: "20:00", 13: "21:00",
};
export const ROOM_TYPES = ["classroom", "lecture_hall", "auditorium", "seminar_hall", "outdoor", "lab_cs", "lab_elec", "lab_phy", "workshop", "lab_civil"];
export const NEEDS = ["projector", "pa_system", "stage", "computers", "accessible", "power_benches", "ac"];

export function todayCode(): string {
  return ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][new Date().getDay()];
}

export function slotLabel(day: string, a: number, b?: number) {
  const end = b ?? a;
  return a === end ? `${day} P${a}` : `${day} P${a}–P${end}`;
}

// ------------------------------------------------------------------ calendar dates (YYYY-MM-DD, no time zone)
const WEEKDAY = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const asUTC = (iso: string) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };

export function dayOfDate(iso: string): string {
  return WEEKDAY[asUTC(iso).getUTCDay()];
}

export function addDays(iso: string, n: number): string {
  const d = asUTC(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** First date on or after `from` that falls on `day` (MON..SUN). */
export function nextDateFor(day: string, from: string): string {
  for (let i = 0; i < 7; i++) if (dayOfDate(addDays(from, i)) === day) return addDays(from, i);
  return from;
}

export function datesBetween(a: string, b: string): string[] {
  const out: string[] = [];
  for (let d = a; d <= b && out.length < 62; d = addDays(d, 1)) out.push(d);
  return out;
}

export function fmtDate(iso: string): string {
  return asUTC(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

/** Contiguous blocks a lab may occupy (P2–P4 and P6–P8 on the CIT calendar); mirrors guardrails.lab_windows. */
export function labWindows(calendar: any): number[][] {
  const starts: number[] = calendar?.lab_start_periods ?? [2, 6];
  return starts.map((s) => [s, s + 1, s + 2]);
}

const inLabWindow = (calendar: any, start: number, duration: number) =>
  labWindows(calendar).some((w) => start >= w[0] && start + duration - 1 <= w[w.length - 1]);

/** Legal start periods for an academic class: no lunch crossing, labs at the lab starts and inside a lab window, ends by the cutoff period. */
export function academicStarts(calendar: any, kind: string, duration: number, lastPeriod: number): number[] {
  const lunch = calendar?.lunch?.after_period ?? 5;
  const labStarts: number[] = calendar?.lab_start_periods ?? [2, 6];
  const out: number[] = [];
  for (let t = 1; t + duration - 1 <= lastPeriod; t++) {
    if (t <= lunch && lunch < t + duration - 1) continue;
    if (kind === "lab" && (!labStarts.includes(t) || !inLabWindow(calendar, t, duration))) continue;
    out.push(t);
  }
  return out;
}

/** Why a class request is malformed on its own (strict room type, lab windows); mirrors guardrails.class_request_problems. */
export function classRequestProblems(calendar: any, kind: string, roomTypes: string[], start: number, duration: number,
  room?: { id: string; type: string; allowed_uses: string[] }): string[] {
  const out: string[] = [];
  if (!room || !kind) return out;
  if (!roomTypes.includes(room.type)) out.push(`${room.id} is a ${room.type.replace("_", " ")}; this ${kind} needs a ${roomTypes.map((t) => t.replace("_", " ")).join(" / ")} room`);
  else if (!room.allowed_uses.includes(kind)) out.push(`${room.id} (${room.type.replace("_", " ")}) does not take ${kind}s`);
  if (start && (kind === "lab" || room.allowed_uses.includes("lab")) && !inLabWindow(calendar, start, duration))
    out.push(`Labs run only in ${labWindows(calendar).map((w) => `P${w[0]}–P${w[w.length - 1]}`).join(" or ")}; P${start}–P${start + duration - 1} falls outside them`);
  return out;
}

/** "Extra lab CS191 (F03) · Y1-A → Wed 12 Nov, P2–P4 · CS-LAB-1" */
export function requestLabel(r: ClassRequest) {
  const what = r.kind === "move" ? `Move ${r.session_id}` : `Extra ${r.session_kind} ${r.course_id}`;
  const when = `${r.date ? fmtDate(r.date) : r.day}, P${r.start_period}${r.end_period > r.start_period ? `–P${r.end_period}` : ""}`;
  return `${what} (${r.faculty_id}) · ${r.groups.join(", ")} → ${when} · ${r.room_id}`;
}

export function periodRange(calendar: any, a: number, b: number): string {
  const per: Record<number, { start: string; end: string }> = Object.fromEntries((calendar?.periods ?? []).map((p: any) => [p.id, p]));
  return `${per[a]?.start ?? PERIOD_TIME[a]}–${per[b]?.end ?? ""}`;
}
