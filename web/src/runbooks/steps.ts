// Canonical runbook steps (contracts/runbook.schema.json, spec §7): checks the step editor blocks saving on.
import type { ActionInfo, CanonicalStep } from "../api/types";
import { SLUG } from "../components/format";

const STEP_DURATION = /^[1-9][0-9]*(s|m|h)$/;

/** max(step.approval, action.min_approval): a runbook may raise the level, never lower it (AGENTS.md). */
export function effectiveApproval(step: CanonicalStep, actions: ActionInfo[]): "none" | "required" {
  const min = step.kind === "action" ? actions.find((a) => a.name === step.action)?.minApproval : undefined;
  return step.approval === "required" || min === "required" ? "required" : "none";
}

export function stepProblems(steps: CanonicalStep[], actions: ActionInfo[]): string[] {
  const out: string[] = [];
  const ids = new Set<string>();
  steps.forEach((s, i) => {
    const n = `Step ${i + 1}`;
    if (!SLUG.test(s.id)) out.push(`${n}: id must be lowercase letters, digits, '-', '_' or '.'`);
    if (ids.has(s.id)) out.push(`${n}: id "${s.id}" is used twice`);
    ids.add(s.id);
    if (s.kind === "text" && !s.text?.trim()) out.push(`${n}: write what to do`);
    if (s.kind === "wait" && !STEP_DURATION.test(s.duration ?? "")) out.push(`${n}: wait needs a duration like 2m`);
    if (s.kind === "action") {
      const action = actions.find((a) => a.name === s.action);
      if (!action) out.push(`${n}: choose an action`);
      const required = (action?.paramsSchema as { required?: string[] } | undefined)?.required ?? [];
      for (const p of required) {
        const v = s.params?.[p];
        if (v === undefined || v === "") out.push(`${n}: parameter “${p}” is required`);
      }
    }
    if (s.attempts !== undefined && (!Number.isInteger(s.attempts) || s.attempts < 1)) out.push(`${n}: attempts must be 1 or more`);
  });
  steps.forEach((s, i) => {
    if (s.on_failure && !ids.has(s.on_failure)) out.push(`Step ${i + 1}: “on failure” points to a missing step`);
    if (s.on_failure === s.id) out.push(`Step ${i + 1}: “on failure” cannot point to itself`);
  });
  return out;
}

/** Only the keys that belong to the step's kind, so a kind change does not leave stale fields behind. */
export function cleanStep(step: CanonicalStep): CanonicalStep {
  const common: (keyof CanonicalStep)[] = ["id", "kind", "title", "approval", "approval_role", "attempts", "on_failure", "resolves", "only_if_open", "needs_review"];
  const byKind: Record<CanonicalStep["kind"], (keyof CanonicalStep)[]> = {
    text: ["text", "checkbox"],
    action: ["action", "params"],
    wait: ["duration"],
  };
  const keep = new Set([...common, ...byKind[step.kind]]);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(step)) {
    if (!keep.has(k as keyof CanonicalStep) || v === undefined || v === "" || v === null) continue;
    if (k === "on_failure" && !v) continue;
    out[k] = v;
  }
  return out as unknown as CanonicalStep;
}

export function newStepId(steps: CanonicalStep[]): string {
  let i = steps.length + 1;
  while (steps.some((s) => s.id === `step-${i}`)) i++;
  return `step-${i}`;
}

export function stepLabel(s: CanonicalStep): string {
  if (s.title) return s.title;
  if (s.kind === "text") return s.text?.split("\n")[0] ?? "";
  if (s.kind === "wait") return `Wait ${s.duration ?? ""}`;
  return s.action ?? "";
}
