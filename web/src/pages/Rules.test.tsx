import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";

import type { RuleDoc } from "../api/types";
import { ME_MANAGER, ME_MEMBER, renderWith, stubApi } from "../test-utils";
import { RuleEditor } from "./RuleEditor";
import { Rules } from "./Rules";

afterEach(() => vi.unstubAllGlobals());

const RULES: RuleDoc[] = [
  {
    id: "rates-large-rejections",
    owner: { team: "rates" },
    enabled: true,
    when: { all: [{ "payload.notional": { gt: 10000000 } }] },
    route: [{ to: { team: "rates" }, channels: ["email"] }],
    present: { title: "Rejected {{payload.currency}} {{payload.notional}}" },
    updatedBy: "mia",
    updatedAt: "2026-11-21T10:00:00Z",
  },
  { id: "dave-own", owner: { user: "dave" }, enabled: false, when: { $service: "x" }, route: [], updatedBy: "dave", updatedAt: "2026-11-21T10:00:00Z" },
];

const common = { "GET /rules": RULES, "GET /users": [], "GET /teams": [{ id: "rates", name: "Rates", members: [] }], "GET /runbooks": [] };

it("a member may toggle only their personal rule", async () => {
  stubApi({ ...common, "GET /me": ME_MEMBER });
  renderWith(<Rules />);
  const teamRow = (await screen.findByText("rates-large-rejections")).closest<HTMLElement>("[data-rule]")!;
  await waitFor(() => expect(within(teamRow).getByRole("switch")).toBeDisabled());
  expect(within(teamRow).getByRole("link", { name: "View" })).toBeInTheDocument();
  const ownRow = screen.getByText("dave-own").closest<HTMLElement>("[data-rule]")!;
  expect(within(ownRow).getByRole("switch")).toBeEnabled();
  expect(within(ownRow).getByRole("link", { name: "Edit" })).toBeInTheDocument();
});

it("a manager enables the team rule with PATCH {enabled}", async () => {
  const calls = stubApi({ ...common, "GET /me": ME_MANAGER, "PATCH /rules/rates-large-rejections": (b: unknown) => ({ ...RULES[0], ...(b as object) }) });
  renderWith(<Rules />);
  const row = (await screen.findByText("rates-large-rejections")).closest<HTMLElement>("[data-rule]")!;
  await waitFor(() => expect(within(row).getByRole("switch")).toBeEnabled());
  fireEvent.click(within(row).getByRole("switch"));
  await waitFor(() => expect(calls).toContainEqual({ method: "PATCH", path: "/rules/rates-large-rejections", body: { enabled: false } }));
});

it("the editor is read only for a member on a team rule", async () => {
  stubApi({ ...common, "GET /me": ME_MEMBER, "GET /rules/rates-large-rejections": RULES[0] });
  renderWith(
    <Routes>
      <Route path="/rules/:id" element={<RuleEditor />} />
    </Routes>,
    "/rules/rates-large-rejections",
  );
  expect(await screen.findByText(/Only a manager of team rates or an admin can change it/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  // the tester stays available
  expect(screen.getByRole("button", { name: "Test rule" })).toBeInTheDocument();
});

it("a manager saves an untouched rule with exactly the stored document", async () => {
  const calls = stubApi({ ...common, "GET /me": ME_MANAGER, "GET /rules/rates-large-rejections": RULES[0], "PUT /rules/rates-large-rejections": RULES[0] });
  renderWith(
    <Routes>
      <Route path="/rules/:id" element={<RuleEditor />} />
    </Routes>,
    "/rules/rates-large-rejections",
  );
  const save = await screen.findByRole("button", { name: "Save changes" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  const { updatedBy: _b, updatedAt: _a, ...stored } = RULES[0];
  void _b;
  void _a;
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual(stored));
});
