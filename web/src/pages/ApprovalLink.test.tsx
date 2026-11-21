import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApprovalLink } from "./ApprovalLink";

const PAGE = {
  approval: { id: 5, alertId: 7, runId: 2, stepId: "escalate", team: "settlements", requiredRole: "manager",
              status: "pending", createdAt: "2026-11-21T10:00:00Z" },
  alert: { id: 7, title: "Settlement failed: wf-42", severity: "critical", state: "open", teams: ["settlements"] },
  step: { id: "escalate", kind: "action", title: "Escalate to counterparty-support", action: "escalate",
          params: { team: "counterparty-support" }, effectiveApproval: "required" },
  userId: "carol",
}; // fmt: skip

function renderAt(token: string) {
  render(
    <MantineProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[`/approvals/${token}`]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path="/approvals/:token" element={<ApprovalLink />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

it("shows what is approved and records the decision", async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ ...PAGE.approval, status: "approved", decidedBy: "carol", via: "email" }))
      : new Response(JSON.stringify(PAGE)),
  );
  vi.stubGlobal("fetch", fetchMock);
  renderAt("signed.token-value");

  expect(await screen.findByText("Escalate to counterparty-support")).toBeInTheDocument();
  expect(screen.getByText("You are deciding as carol.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText(/Approved by carol/)).toBeInTheDocument();
  const [url, init] = fetchMock.mock.calls[1];
  expect(url).toBe("/api/approvals/by-token/signed.token-value");
  expect(JSON.parse(String(init?.body))).toEqual({ decision: "approve" });
});

it("explains an expired link", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "expired", message: "x" } }), { status: 410 })));
  renderAt("old.token-value-xx");
  expect(await screen.findByText(/has expired/)).toBeInTheDocument();
});
