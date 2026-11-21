import { describe, expect, it } from "vitest";

import type { ActionInfo, CanonicalStep } from "../api/types";
import { cleanStep, effectiveApproval, stepProblems } from "./steps";

const schema = (required: string[]) => ({ required }) as unknown as ActionInfo["paramsSchema"];

const actions: ActionInfo[] = [
  { name: "http_get", paramsSchema: schema(["url"]), minApproval: "none" },
  { name: "restart_service", paramsSchema: schema(["service"]), minApproval: "required" },
];

describe("runbook steps", () => {
  it("never lowers an action's minimum approval", () => {
    expect(effectiveApproval({ id: "a", kind: "action", action: "restart_service", approval: "none" }, actions)).toBe("required");
    expect(effectiveApproval({ id: "a", kind: "action", action: "http_get", approval: "required" }, actions)).toBe("required");
    expect(effectiveApproval({ id: "a", kind: "action", action: "http_get" }, actions)).toBe("none");
  });

  it("finds problems that would make the runbook invalid", () => {
    const steps: CanonicalStep[] = [
      { id: "check", kind: "action", action: "http_get", params: {} },
      { id: "check", kind: "wait", duration: "soon" },
      { id: "x", kind: "text", text: " ", on_failure: "nowhere" },
    ];
    expect(stepProblems(steps, actions)).toEqual([
      "Step 1: parameter “url” is required",
      'Step 2: id "check" is used twice',
      "Step 2: wait needs a duration like 2m",
      "Step 3: write what to do",
      "Step 3: “on failure” points to a missing step",
    ]);
  });

  it("drops fields of another kind after a kind change", () => {
    expect(cleanStep({ id: "a", kind: "wait", duration: "2m", action: "http_get", params: { url: "x" }, text: "" })).toEqual({ id: "a", kind: "wait", duration: "2m" });
  });
});
