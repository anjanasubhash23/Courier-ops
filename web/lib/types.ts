// Mirrors app/schemas.py.

export type Role = "viewer" | "operator" | "supervisor" | "admin";
export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, supervisor: 2, admin: 3 };

export type Severity = "low" | "medium" | "high";
export type ActionStatus = "proposed" | "rejected" | "executed" | "failed";

export const RULES = [
  "pickup_overdue",
  "stuck_at_hub",
  "stuck_at_delivery_center",
  "in_transit_overdue",
  "delivery_failed",
  "misrouted",
  "sla_breach",
] as const;
export type Rule = (typeof RULES)[number];

export interface Me {
  id: number;
  username: string;
  role: Role;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExceptionRow {
  id: number;
  shipment_id: number;
  tracking_number: string;
  rule: Rule;
  severity: Severity;
  status: "open" | "resolved";
  hub: string | null;
  details: Record<string, string | number>;
  detected_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  pending_actions: number;
}

export interface Shipment {
  tracking_number: string;
  service_level: string;
  status: string;
  origin_hub: string;
  destination_hub: string;
  origin_pincode: string;
  destination_pincode: string;
  weight_grams: number;
  booked_at: string;
  promised_by: string;
  last_event_at: string | null;
  last_scan_hub: string | null;
  delivered_at: string | null;
}

export interface TrackingEvent {
  event_type: string;
  occurred_at: string;
  hub: string | null;
  reason: string | null;
}

export interface Action {
  id: number;
  exception_id: number;
  action_type: string;
  params: Record<string, string>;
  rationale: string | null;
  status: ActionStatus;
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  executed_at: string | null;
  result: Record<string, string | number> | null;
}

export interface TriageRun {
  id: number;
  cause: string;
  confidence: number;
  summary: string;
  evidence: string[];
  diagnoser: string;
  created_at: string;
  action_ids: number[];
}

export interface ExceptionDetail extends ExceptionRow {
  shipment: Shipment;
  timeline: TrackingEvent[];
  actions: Action[];
  allowed_actions: string[];
  triage: TriageRun[];
}

export interface Summary {
  as_of: string | null;
  shipments: number;
  by_status: Record<string, number>;
  delivered: number;
  delivered_on_time_pct: number | null;
  open_exceptions: number;
  open_by_rule: Record<string, number>;
  open_by_severity: Record<string, number>;
  actions_by_status: Record<string, number>;
}

export interface Hub {
  code: string;
  name: string;
  city: string;
  hub_type: string;
  shipments_here: number;
  open_exceptions: number;
}

export interface Answer {
  ok: boolean;
  question: string;
  sql: string | null;
  note: string | null;
  columns: string[];
  rows: (string | number | boolean | null)[][];
  truncated: boolean;
  error: string | null;
  attempts: { sql: string | null; error: string | null }[];
  model: string;
}
