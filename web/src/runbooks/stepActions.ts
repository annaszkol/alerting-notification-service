// Which buttons a step of a run offers (spec §7, §9 P2). The API checks permissions; this only hides what
// cannot apply to the step's state.
import type { RenderedStep, StepRun } from "../api/types";

export type StepAction = "done" | "run" | "approve" | "reject";

export function stepActions(step: RenderedStep, run: StepRun | undefined): StepAction[] {
  if (!run || run.id === null || run.id === undefined) return []; // not reached yet
  if (step.kind === "text" && run.status === "pending") return ["done"];
  if (step.kind === "action" && run.status === "pending" && !run.nextRunAt) return ["run"];
  if (run.status === "waiting_approval" && run.approvalId) return ["approve", "reject"];
  return [];
}

export const STEP_STATUS_COLOR: Record<StepRun["status"], string> = {
  pending: "gray",
  scheduled: "blue",
  running: "blue",
  waiting_approval: "grape",
  succeeded: "green",
  done: "green",
  failed: "red",
  rejected: "red",
  skipped: "gray",
};

/** One line about who did what on a step, for the run view. */
export function stepWho(run: StepRun | undefined): string {
  if (!run) return "";
  const parts = [];
  if (run.startedBy) parts.push(`started by ${run.startedBy}`);
  if (run.approvedBy) parts.push(`${run.status === "rejected" ? "rejected" : "approved"} by ${run.approvedBy}`);
  if (run.executedBy) parts.push(`done by ${run.executedBy}`);
  if (run.attempt > 1) parts.push(`${run.attempt} attempts`);
  return parts.join(" · ");
}
