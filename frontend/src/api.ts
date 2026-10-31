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
