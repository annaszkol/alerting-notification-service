import { screen } from "@testing-library/react";

import type { AlertRunbook, RunbookRun } from "../api/types";
import { renderWith, stubApi } from "../test-utils";
import { RunbookPanel } from "./RunbookPanel";

afterEach(() => vi.unstubAllGlobals());

const runbook: AlertRunbook = {
  id: "settlement-timeout",
  title: "Settlement timeout",
  format: "yaml",
  approved: true,
  mode: "manual",
  runId: 5,
  steps: [
    { id: "check", kind: "text", title: "Check", text: "Check the dashboard", effectiveApproval: "none" },
    { id: "escalate", kind: "action", title: "Escalate", action: "escalate", effectiveApproval: "required", approvalRole: "manager" },
  ],
};

const run: RunbookRun = {
  id: 5,
  alertId: 7,
  runbookId: "settlement-timeout",
  status: "waiting_approval",
  startedBy: "dave",
  startedAt: "2026-11-21T10:00:00Z",
  steps: [
    { id: 11, stepId: "check", status: "pending", attempt: 1 },
    { id: 12, stepId: "escalate", status: "waiting_approval", attempt: 1, approvalId: 99 },
  ],
};

it("a member works on steps but cannot approve a manager step", async () => {
  stubApi({ "GET /runbook-runs/5": run });
  renderWith(<RunbookPanel alertId={7} runbook={runbook} canRun decidable={new Set()} />);
  expect(await screen.findByRole("button", { name: "Done" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  expect(screen.getByText("waiting for a manager to approve")).toBeInTheDocument();
});

it("the approver sees Approve / Reject", async () => {
  stubApi({ "GET /runbook-runs/5": run });
  renderWith(<RunbookPanel alertId={7} runbook={runbook} canRun decidable={new Set([99])} />);
  expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
});

it("someone outside the alert's teams gets no run buttons", async () => {
  stubApi({ "GET /runbook-runs/5": { ...run, status: "succeeded" } });
  renderWith(<RunbookPanel alertId={7} runbook={runbook} canRun={false} decidable={new Set()} />);
  expect(await screen.findByText("succeeded")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run again|start run/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
});
