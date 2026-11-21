// The live alert feed: REST page + WebSocket upserts, following contracts/ws-messages.md.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, queryString } from "../api/client";
import type { AlertPage, AlertSummary } from "../api/types";
import { applyUpsert, DEFAULT_STATES, mergePage, sortAlerts, type FeedMap, type Filters } from "./feedState";
import { useLive } from "./live";

const PAGE_SIZE = 50;

export function useAlertsFeed(filters: Filters) {
  const live = useLive();
  const [feed, setFeed] = useState<FeedMap>(new Map());
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const key = JSON.stringify(filters);

  const params = useCallback(
    (after?: string | null) =>
      queryString({
        team: filtersRef.current.team,
        severity: filtersRef.current.severity,
        state: filtersRef.current.state?.length ? filtersRef.current.state : DEFAULT_STATES,
        q: filtersRef.current.q,
        cursor: after,
        limit: String(PAGE_SIZE),
      }),
    [],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api<AlertPage>(`/alerts${params()}`);
      setFeed((held) => mergePage(held, page.items, filtersRef.current));
      setCursor(page.nextCursor ?? null);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [params]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    const page = await api<AlertPage>(`/alerts${params(cursor)}`);
    setFeed((held) => mergePage(held, page.items, filtersRef.current, true));
    setCursor(page.nextCursor ?? null);
  }, [cursor, params]);

  // New filters: tell the server, start from an empty list, reload when the server confirms (rule 1).
  useEffect(() => {
    setFeed(new Map());
    live.setFilters({
      team: filters.team ?? undefined,
      severity: filters.severity?.length ? filters.severity : undefined,
      state: filters.state?.length ? filters.state : DEFAULT_STATES,
    });
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures every filter field
  }, [key]);

  useEffect(
    () =>
      live.listen((message) => {
        if (message.type === "alert.upsert") setFeed((held) => applyUpsert(held, message.alert, filtersRef.current));
        else if (message.type === "subscribed" || message.type === "resync") void reload();
      }),
    [live, reload],
  );

  const alerts: AlertSummary[] = useMemo(() => sortAlerts(feed.values()), [feed]);
  return { alerts, loading, error, hasMore: cursor !== null, loadMore, status: live.status };
}
