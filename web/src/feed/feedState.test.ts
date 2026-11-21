import type { AlertSummary } from "../api/types";
import { applyUpsert, isNewer, mergePage, sortAlerts, visible, type FeedMap } from "./feedState";

function alert(id: number, over: Partial<AlertSummary> = {}): AlertSummary {
  return {
    id,
    ruleId: "r",
    owner: { team: "rates" },
    teams: ["rates"],
    state: "open",
    severity: "error",
    title: `alert ${id}`,
    count: 1,
    firstSeen: "2026-11-21T10:00:00Z",
    lastSeen: "2026-11-21T10:00:00Z",
    hasDeadNotification: false,
    version: "0001",
    ...over,
  } as AlertSummary;
}

const empty: FeedMap = new Map();

describe("versions (ws-messages rule 2)", () => {
  it("applies only newer versions", () => {
    let feed = applyUpsert(empty, alert(1, { count: 1, version: "0002" }), {});
    feed = applyUpsert(feed, alert(1, { count: 5, version: "0001" }), {}); // late, older message
    expect(feed.get(1)?.count).toBe(1);
    feed = applyUpsert(feed, alert(1, { count: 7, version: "0003" }), {});
    expect(feed.get(1)?.count).toBe(7);
  });

  it("compares versions as strings", () => {
    expect(isNewer(alert(1, { version: "2026-11-21T10:00:01Z" }), alert(1, { version: "2026-11-21T10:00:00Z" }))).toBe(true);
    expect(isNewer(alert(1, { version: "a" }), undefined)).toBe(true);
  });

  it("returns the same map when nothing changed (no re-render)", () => {
    const feed = applyUpsert(empty, alert(1, { version: "0002" }), {});
    expect(applyUpsert(feed, alert(1, { version: "0001" }), {})).toBe(feed);
  });
});

describe("client-side filters", () => {
  it("drops an alert that left the state filter (e.g. resolved)", () => {
    let feed = applyUpsert(empty, alert(1), {});
    feed = applyUpsert(feed, alert(1, { state: "resolved", version: "0002" }), {});
    expect(feed.has(1)).toBe(false);
  });

  it("default states are open and acknowledged", () => {
    expect(visible(alert(1, { state: "acknowledged" }), {})).toBe(true);
    expect(visible(alert(1, { state: "resolved" }), {})).toBe(false);
    expect(visible(alert(1, { state: "resolved" }), { state: ["resolved"] })).toBe(true);
  });

  it("applies team, severity and title search", () => {
    const a = alert(1, { teams: ["rates", "payments"], severity: "critical", title: "Rejected EUR 25000000" });
    expect(visible(a, { team: "payments" })).toBe(true);
    expect(visible(a, { team: "pricing" })).toBe(false);
    expect(visible(a, { severity: ["warning"] })).toBe(false);
    expect(visible(a, { q: "eur 25" })).toBe(true);
    expect(visible(a, { q: "usd" })).toBe(false);
  });
});

describe("REST page vs WebSocket race (rule 1)", () => {
  it("keeps a newer version that arrived over the socket", () => {
    const held = applyUpsert(empty, alert(1, { count: 9, version: "0005" }), {});
    const merged = mergePage(held, [alert(1, { count: 3, version: "0004" }), alert(2)], {});
    expect(merged.get(1)?.count).toBe(9);
    expect(merged.has(2)).toBe(true);
  });

  it("replaces the list on reload, appends on 'load more'", () => {
    const held = mergePage(empty, [alert(1), alert(2)], {});
    expect([...mergePage(held, [alert(3)], {}).keys()]).toEqual([3]);
    expect([...mergePage(held, [alert(3)], {}, true).keys()].sort()).toEqual([1, 2, 3]);
  });
});

it("sorts by severity, then most recent", () => {
  const sorted = sortAlerts([
    alert(1, { severity: "info", lastSeen: "2026-11-21T12:00:00Z" }),
    alert(2, { severity: "critical", lastSeen: "2026-11-21T09:00:00Z" }),
    alert(3, { severity: "critical", lastSeen: "2026-11-21T11:00:00Z" }),
  ]);
  expect(sorted.map((a) => a.id)).toEqual([3, 2, 1]);
});
