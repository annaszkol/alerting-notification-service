// Pure feed logic (contracts/ws-messages.md "Client rules"), kept free of React so it is easy to test.
import type { AlertState, AlertSummary, Severity } from "../api/types";

export interface Filters {
  team?: string | null;
  severity?: Severity[];
  state?: AlertState[];
  q?: string;
}

export const DEFAULT_STATES: AlertState[] = ["open", "acknowledged"];
const SEVERITY_RANK: Record<Severity, number> = { critical: 0, error: 1, warning: 2, info: 3 };

export type FeedMap = ReadonlyMap<number, AlertSummary>;

/** Versions are sortable strings; a message that is not newer than what we hold is dropped (rule 2). */
export function isNewer(incoming: AlertSummary, current: AlertSummary | undefined): boolean {
  return current === undefined || incoming.version > current.version;
}

/** The server filters by team / user / severity; the client applies state (rule in "Server -> client") and q. */
export function visible(alert: AlertSummary, filters: Filters): boolean {
  const states = filters.state?.length ? filters.state : DEFAULT_STATES;
  if (!states.includes(alert.state)) return false;
  if (filters.severity?.length && !filters.severity.includes(alert.severity)) return false;
  if (filters.team && !alert.teams.includes(filters.team)) return false;
  const q = filters.q?.trim().toLowerCase();
  return !q || alert.title.toLowerCase().includes(q);
}

/** alert.upsert: newer versions replace, and an alert that left the filters (e.g. resolved) is removed. */
export function applyUpsert(feed: FeedMap, alert: AlertSummary, filters: Filters): FeedMap {
  if (!isNewer(alert, feed.get(alert.id))) return feed;
  const next = new Map(feed);
  if (visible(alert, filters)) next.set(alert.id, alert);
  else next.delete(alert.id);
  return next;
}

/** A REST page is authoritative, except for alerts where the WebSocket already delivered a newer version. */
export function mergePage(feed: FeedMap, items: AlertSummary[], filters: Filters, append = false): FeedMap {
  const next = new Map<number, AlertSummary>(append ? feed : []);
  for (const item of items) {
    const held = feed.get(item.id);
    const chosen = held && !isNewer(item, held) ? held : item;
    if (visible(chosen, filters)) next.set(chosen.id, chosen);
  }
  return next;
}

/** Most severe first, then most recently seen. */
export function sortAlerts(alerts: Iterable<AlertSummary>): AlertSummary[] {
  return [...alerts].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.lastSeen.localeCompare(a.lastSeen),
  );
}
