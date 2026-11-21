import type { RenderedStep, StepRun } from "../api/types";
import { stepActions, stepWho } from "./stepActions";

const step = (kind: RenderedStep["kind"]) => ({ id: "s", kind, title: "s", effectiveApproval: "none" }) as RenderedStep;
const run = (over: Partial<StepRun>) => ({ id: 1, stepId: "s", status: "pending", attempt: 0, ...over }) as StepRun;

it("offers the right buttons per step state", () => {
  expect(stepActions(step("text"), run({}))).toEqual(["done"]);
  expect(stepActions(step("text"), run({ status: "done" }))).toEqual([]);
  expect(stepActions(step("action"), run({ nextRunAt: null }))).toEqual(["run"]);
  expect(stepActions(step("action"), run({ nextRunAt: "2026-11-21T10:00:00Z" }))).toEqual([]); // already queued
  expect(stepActions(step("action"), run({ status: "waiting_approval", approvalId: 7 }))).toEqual(["approve", "reject"]);
  expect(stepActions(step("action"), run({ id: null }))).toEqual([]); // not reached yet
  expect(stepActions(step("action"), undefined)).toEqual([]);
});

it("says who did what", () => {
  expect(stepWho(run({ startedBy: "mia", approvedBy: "marek", attempt: 2, status: "failed" }))).toBe(
    "started by mia · approved by marek · 2 attempts",
  );
  expect(stepWho(run({ status: "rejected", approvedBy: "marek" }))).toBe("rejected by marek");
  expect(stepWho(run({ executedBy: "dave", status: "done" }))).toBe("done by dave");
});
