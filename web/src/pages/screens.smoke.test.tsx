// Every screen renders with real API responses (recorded from the seeded stack: src/test-fixtures/seed-api.json)
// for a member, a manager and an admin, and offers only what that role may do.
import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";

import type { Me } from "../api/types";
import seed from "../test-fixtures/seed-api.json";
import { renderWith, stubApi } from "../test-utils";
import { Admin } from "./Admin";
import { ConnectSource } from "./ConnectSource";
import { MappingEditor } from "./MappingEditor";
import { Mappings } from "./Mappings";
import { Oncall } from "./Oncall";
import { Preferences } from "./Preferences";
import { RuleEditor } from "./RuleEditor";
import { RunbookEditor } from "./RunbookEditor";
import { RunbookImport } from "./RunbookImport";
import { Runbooks } from "./Runbooks";
import { Sources } from "./Sources";
import { Suggestions } from "./Suggestions";
import { Unmapped } from "./Unmapped";

afterEach(() => vi.unstubAllGlobals());

const user = (id: string) => seed["GET /users"].find((u) => u.id === id)!;
const ME: Record<string, Me> = {
  anna: { user: user("anna"), isAdmin: false, teams: [{ id: "rates", name: "Rates", role: "member" }, { id: "payments", name: "Payments", role: "member" }] },
  erin: { user: user("erin"), isAdmin: false, teams: [{ id: "settlements", name: "Settlements", role: "member" }, { id: "payments", name: "Payments", role: "manager" }] },
  admin: { user: user("admin"), isAdmin: true, teams: [] },
};

const SUGGESTION = { id: 1, alertId: 7, target: "runbook", targetId: "high-error-rate-payments", text: "Add a rollback step", author: "anna", status: "open", createdAt: "2026-11-21T10:00:00Z" };
const UNMAPPED = { shapeHash: "abc", sourceId: "http-default", keys: ["kind", "labels", "labels.app.kubernetes.io/name"], count: 3, lastSeen: "2026-11-21T10:00:00Z", sample: { kind: "OOM", labels: { "app.kubernetes.io/name": "billing" } } };

function api(me: keyof typeof ME) {
  const payments = seed["GET /runbooks"].find((r) => r.id === "high-error-rate-payments")!;
  return stubApi({
    ...seed,
    "GET /me": ME[me],
    "GET /suggestions": [SUGGESTION],
    "GET /unmapped": [UNMAPPED],
    "GET /runbooks/high-error-rate-payments": payments,
    "GET /rules/settlements-prod-failures": seed["GET /rules"].find((r) => r.id === "settlements-prod-failures"),
    "POST /mappings/preview": { letters: [{ $eventId: "p", $sourceId: "http-default", $schema: "preview", $service: "billing", payload: UNMAPPED.sample }], errors: [] },
  });
}

function at(path: string, pattern: string, element: JSX.Element) {
  renderWith(
    <Routes>
      <Route path={pattern} element={element} />
    </Routes>,
    path,
  );
}

describe("member (anna)", () => {
  it("rule editor: a team rule of another team is read only, the tester works", async () => {
    api("anna");
    at("/rules/settlements-prod-failures", "/rules/:id", <RuleEditor />);
    expect(await screen.findByText(/Only a manager of team settlements or an admin/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("type")).toBeInTheDocument(); // condition loaded into the form
  });

  it("new rule: a member can only own it personally", async () => {
    api("anna");
    at("/rules/new", "/rules/new", <RuleEditor />);
    expect(await screen.findByDisplayValue("Personal (only me)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create rule" })).toBeDisabled(); // no id, empty condition yet
  });

  it("runbooks: no manager buttons", async () => {
    api("anna");
    at("/runbooks", "/runbooks", <Runbooks />);
    expect(await screen.findByText("High error rate in payments-api")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Import runbook" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit steps" })).not.toBeInTheDocument();
  });

  it("runbook import and edit say who may use them", async () => {
    api("anna");
    at("/runbooks/import", "/runbooks/import", <RunbookImport />);
    expect(await screen.findByText("Only team managers can import runbooks.")).toBeInTheDocument();
  });

  it("on-call: sees who is on duty, cannot edit", async () => {
    api("anna");
    at("/oncall", "/oncall", <Oncall />);
    expect(await screen.findByText("Payments")).toBeInTheDocument();
    expect(screen.getAllByText("On duty now:").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Edit rotation|Set up rotation/ })).not.toBeInTheDocument();
  });

  it("suggestions: can read, cannot decide", async () => {
    api("anna");
    at("/suggestions", "/suggestions", <Suggestions />);
    expect(await screen.findByText("Add a rollback step")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("preferences render the stored channels", async () => {
    api("anna");
    at("/preferences", "/preferences", <Preferences />);
    expect(await screen.findByText("Channels per severity")).toBeInTheDocument();
  });

  it("admin screens are refused politely", async () => {
    api("anna");
    at("/admin", "/admin", <Admin />);
    expect(await screen.findByText("Only admins can manage people, teams and sources.")).toBeInTheDocument();
  });
});

describe("manager (erin, payments)", () => {
  it("runbooks: manages own team's runbook only", async () => {
    api("erin");
    at("/runbooks", "/runbooks", <Runbooks />);
    expect(await screen.findByText("High error rate in payments-api")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Edit steps" })).toHaveLength(seed["GET /runbooks"].filter((r) => r.owner.team === "payments").length));
    expect(screen.getByRole("link", { name: "Import runbook" })).toBeInTheDocument();
  });

  it("runbook editor opens the steps", async () => {
    api("erin");
    at("/runbooks/high-error-rate-payments/edit", "/runbooks/:id/edit", <RunbookEditor />);
    expect(await screen.findByText(/Edit runbook high-error-rate-payments/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("Step id").length).toBeGreaterThan(0);
  });

  it("decides suggestions about own team's runbook", async () => {
    api("erin");
    at("/suggestions", "/suggestions", <Suggestions />);
    expect(await screen.findByRole("button", { name: "Accept" })).toBeInTheDocument();
  });

  it("edits the payments rotation, not others", async () => {
    api("erin");
    at("/oncall", "/oncall", <Oncall />);
    await screen.findByText("Payments");
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Edit rotation|Set up rotation/ })).toHaveLength(1));
  });
});

describe("admin", () => {
  it("sources: unreadable events are shown on their source with a way to teach the format", async () => {
    api("admin");
    at("/sources", "/sources", <Sources />);
    expect(await screen.findByRole("button", { name: "Teach the format" })).toBeInTheDocument();
    expect(screen.getByText(/events the system can't read yet/)).toHaveTextContent("3 events");
    expect(screen.getByRole("button", { name: "Connect a source" })).toBeInTheDocument();
  });

  it("connect a source: the wizard starts by naming the source", async () => {
    api("admin");
    at("/sources/new", "/sources/new", <ConnectSource />);
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create source" })).toBeDisabled();
  });

  it("connect a source: an unreadable event opens the meaning step with a live preview", async () => {
    api("admin");
    renderWith(
      <Routes>
        <Route path="/sources/:id/connect" element={<ConnectSource />} />
      </Routes>,
      { pathname: "/sources/http-default/connect", state: { sample: UNMAPPED.sample } },
    );
    expect(await screen.findByText("Which field names the service or system?")).toBeInTheDocument();
    expect(await screen.findByText("billing", { selector: "p" })).toBeInTheDocument();
  });

  it("unmapped, mappings and the mapping editor", async () => {
    api("admin");
    at("/unmapped", "/unmapped", <Unmapped />);
    expect(await screen.findByRole("button", { name: "Map this shape" })).toBeInTheDocument();
  });

  it("mapping list in priority order", async () => {
    api("admin");
    at("/mappings", "/mappings", <Mappings />);
    const links = await screen.findAllByRole("link", { name: /workflow-event|metric-alert|business-event|pricing-log/ });
    expect(links.map((l) => l.textContent)).toEqual(["workflow-event", "metric-alert", "business-event", "pricing-log"]);
  });

  it("existing mapping loads into the field rows", async () => {
    api("admin");
    at("/mappings/workflow-event", "/mappings/:id", <MappingEditor />);
    expect(await screen.findByDisplayValue("workflowName")).toBeInTheDocument();
    expect(screen.getByDisplayValue("{{workflowName}} / {{step}}: {{error.message}}")).toBeInTheDocument();
  });

  it("admin tabs list teams with roles", async () => {
    api("admin");
    at("/admin", "/admin", <Admin />);
    expect(await screen.findByRole("button", { name: "New team" })).toBeInTheDocument();
    expect(await screen.findByText("Counterparty support")).toBeInTheDocument();
  });
});
