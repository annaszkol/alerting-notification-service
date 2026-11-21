// Types generated from contracts/openapi.yaml (`npm run gen:api`), re-exported under short names.
import type { components } from "./schema";

type S = components["schemas"];
export type User = S["User"];
export type Me = S["Me"];
export type LoginResponse = S["LoginResponse"];
export type Severity = S["Severity"];
export type AlertState = S["AlertState"];
export type AlertSummary = S["AlertSummary"];
export type AlertDetail = S["AlertDetail"];
export type TimelineEntry = S["TimelineEntry"];
export type NotificationView = S["NotificationView"];

export interface AlertPage {
  items: AlertSummary[];
  nextCursor?: string | null;
}

export const SEVERITIES: Severity[] = ["critical", "error", "warning", "info"];
export const STATES: AlertState[] = ["open", "acknowledged", "resolved"];

export type AlertRunbook = S["AlertRunbook"];
export type RenderedStep = S["RenderedStep"];
export type Runbook = S["Runbook"];
export type RunbookRun = S["RunbookRun"];
export type StepRun = S["StepRun"];
export type Approval = S["Approval"];
export type ApprovalByToken = S["ApprovalByToken"];

export type Owner = S["Owner"];
export type Channel = S["Channel"];
export type TeamRole = S["TeamRole"];
export type FieldError = S["FieldError"];
export type Letter = S["Letter"];
export type ConditionResult = S["ConditionResult"];
export type RuleTestResult = S["RuleTestResult"];
export type ActionInfo = S["ActionInfo"];
export type UnmappedGroup = S["UnmappedGroup"];
export type Preferences = S["Preferences"];
export type OncallSchedule = S["OncallSchedule"];
export type Suggestion = S["Suggestion"];
export type SuggestionInput = S["SuggestionInput"];
export type Team = S["Team"];
export type TeamMember = S["TeamMember"];
export type TeamInput = S["TeamInput"];
export type Source = S["Source"];
export type RouteEntry = S["routeEntry"];

export const CHANNELS: Channel[] = ["ui", "email", "sms", "phone"];

/** Condition JSON (contracts/condition.md, spec §4). Kept loose: the builder parses it (rules/condition.ts). */
export type Condition = Record<string, unknown>;

/** A rule document as stored and edited (RuleInput / Rule); `when` and `route` stay plain JSON. */
export interface RuleDoc {
  id: string;
  owner: Owner;
  enabled?: boolean;
  when: Condition;
  route?: RouteEntry[];
  present?: S["Present"];
  dedup?: S["Dedup"];
  autoResolveAfter?: string | null;
  runbooks?: string[];
  updatedBy?: string;
  updatedAt?: string;
}

/** Schema mapping (spec §3): `fields` maps `$field` -> path | {const} | {from, map, format} | template. */
export interface MappingDoc {
  id: string;
  priority?: number;
  sources?: string[];
  detect?: Condition;
  schemaFrom?: string | null;
  splitOn?: string | null;
  fields: Record<string, unknown>;
}

/** A canonical runbook step (contracts/runbook.schema.json). */
export interface CanonicalStep {
  id: string;
  kind: "text" | "action" | "wait";
  title?: string;
  text?: string;
  checkbox?: boolean;
  action?: string;
  params?: Record<string, unknown>;
  duration?: string;
  approval?: "none" | "required";
  approval_role?: TeamRole;
  attempts?: number;
  on_failure?: string;
  resolves?: boolean;
  only_if_open?: boolean;
  needs_review?: boolean;
}

export interface PreviewResult {
  detected?: boolean;
  letters: Letter[];
  errors: FieldError[];
}

export type WorkStatus = S["WorkStatus"];
