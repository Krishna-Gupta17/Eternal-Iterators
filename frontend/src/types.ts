export type Role = "admin" | "department_coordinator" | "faculty" | "club";

export interface User {
  username: string;
  name: string;
  role: Role;
  role_label: string;
  department?: string;
  faculty_id?: string;
  club?: string;
}

export interface Room {
  id: string;
  name: string;
  building: string;
  type: string;
  capacity: number;
  features: string[];
  accessible: boolean;
  allowed_uses: string[];
}

export interface Meta {
  calendar: any;
  rooms: Room[];
  faculty: { id: string; name: string; department: string }[];
  groups: { id: string; name: string; year: number; department: string; size: number }[];
  courses: { id: string; title: string; department: string }[];
  priority_order: string[];
  priority_labels: Record<string, string>;
  role_category_cap: Record<string, string>;
  llm: { provider: string; model: string | null; configured: boolean };
  dataset_id: string;
  institute: string;
  today: string;
  academic_cutoff: string;
  academic_last_period: number;
  room_types: string[];
}

export interface SessionEntry {
  id: string;
  course_id: string;
  title: string;
  kind: "lecture" | "tutorial" | "lab";
  groups: string[];
  faculty_id: string;
  faculty_name: string;
  department?: string;
  day: string;
  start_period: number;
  duration: number;
  room_id: string;
  size: number;
  basket_slot: string | null;
}

export interface PlacedBooking {
  request_id: string;
  room_id: string;
  day: string;
  start_period: number;
  end_period: number;
  mandatory_for_groups: string[];
  title: string;
}

export interface Window {
  day: string;
  date?: string | null;
  from_period: number;
  to_period: number;
}

export interface BookingRequest {
  id: string;
  submitted_at: string;
  requester: string;
  role: string;
  requested_category: string;
  title: string;
  attendance: number;
  duration_periods: number;
  windows: Window[];
  room_types: string[];
  needs: string[];
  requested_room: string | null;
  mandatory_for_groups: string[];
  raw_text?: string;
}

export interface BookingRecord {
  id: string;
  request: BookingRequest;
  submitted_by: string | null;
  status: "pending" | "approved" | "rejected" | "alternative_offered" | "cancelled";
  placement: { room_id: string; day: string; start_period: number; end_period: number } | null;
  reason: string;
  decided_by: string | null;
  displaces: string[];
  accepted: boolean | null;
  effective_category: string;
  priority_rank: number;
  approver: string | null;
  mine: boolean;
  can_approve: boolean;
}

export interface Scoreboard {
  hard_violations: number;
  hard_by_rule: Record<string, number>;
  violations: { rule: string; where: string; detail: string }[];
  soft_total: number;
  soft_penalties: Record<string, number>;
  booking_score: number | null;
  booking_score_max: number | null;
  priority_inversions: string[];
  exam_soft_penalty: number | null;
  sessions_moved: number | null;
  warnings: string[];
}

export interface Job {
  id: string | null;
  kind: string | null;
  status: "idle" | "running" | "done" | "failed";
  error: string | null;
  result: any;
  elapsed?: number;
}

export interface Summary {
  has_timetable: boolean;
  scoreboard: Scoreboard | null;
  pending_changes: number;
  pending_bookings: number;
  applied_changes: number;
  sessions: number;
  teaching_hours: number;
  requests: number;
  exams: number;
  job: Job;
}

export interface DiffRow {
  session_id: string;
  course_id: string;
  kind: string;
  faculty_id?: string;
  groups?: string[];
  old?: { day: string; start_period: number; room_id: string };
  new?: { day: string; start_period: number; room_id: string };
  change?: string;
}

export interface ChangeItem {
  id: string;
  created_at: string;
  created_by: string;
  requested_by: string | null;
  source: string;
  change_summary: string[];
  status: "pending" | "approved" | "rejected" | "infeasible" | "outdated";
  note?: string;
  queue_note?: string;
  effective_date?: string | null;
  conflicts?: string[];
  diff?: { moved: DiffRow[]; added: DiffRow[]; removed: DiffRow[] };
  moved_count?: number;
  score_before?: { hard: number; soft: number; booking: number | null };
  score_after?: { hard: number; soft: number; booking: number | null };
  released_bookings: string[];
  exam_conflicts?: string[];
  solver_status?: string;
  decided_by?: string;
  can_approve: boolean;
}

export interface MySession {
  id: string;
  course_id: string;
  title: string;
  kind: "lecture" | "tutorial" | "lab";
  groups: string[];
  faculty_id: string;
  size: number;
  duration: number;
  room_types: string[];
  needs_accessible: boolean;
  basket_slot: string | null;
  slot: string;
  day: string;
  start_period: number;
  room_id: string;
}
