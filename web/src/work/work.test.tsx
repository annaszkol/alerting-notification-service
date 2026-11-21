import { fireEvent, screen, waitFor } from "@testing-library/react";

import type { WorkStatus } from "../api/types";
import { renderWith, stubApi } from "../test-utils";
import { hm, WorkButton, WorkTimeSection } from "./work";

afterEach(() => vi.unstubAllGlobals());

const AWAY: WorkStatus = { tracking: false, active: false, startedAt: null, autoStopAt: null, workedTodaySeconds: 0, recent: [] };
const started = new Date(Date.now() - 65 * 60_000).toISOString();
const AT_WORK: WorkStatus = { tracking: true, active: true, startedAt: started, autoStopAt: started, workedTodaySeconds: 3900, recent: [{ startedAt: started, endedAt: null, endedHow: null }] };

it("formats hours and minutes", () => {
  expect(hm(0)).toBe("0:00");
  expect(hm(3900)).toBe("1:05");
});

it("Start work opens a session and shows the timer, Stop ends it", async () => {
  const calls = stubApi({ "GET /me/work": AWAY, "POST /me/work/start": AT_WORK, "POST /me/work/stop": { ...AT_WORK, active: false, startedAt: null } });
  renderWith(<WorkButton />);
  fireEvent.click(await screen.findByRole("button", { name: "Start work" }));
  expect(await screen.findByText("at work 1:05")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() => expect(calls.map((c) => `${c.method} ${c.path}`)).toContain("POST /me/work/stop"));
  expect(await screen.findByRole("button", { name: "Start work" })).toBeInTheDocument();
});

it("the preferences section switches tracking", async () => {
  const calls = stubApi({ "GET /me/work": AT_WORK, "PUT /me/work": { ...AT_WORK, tracking: false } });
  renderWith(<WorkTimeSection />);
  expect(await screen.findByText("Today: 1:05 h")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Switch off" }));
  await waitFor(() => expect(calls).toContainEqual({ method: "PUT", path: "/me/work", body: { tracking: false } }));
});
