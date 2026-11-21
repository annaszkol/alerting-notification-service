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
