// Work time start / stop (spec §6 "Czas pracy"): the header button and shared hooks. With tracking on, email, SMS
// and phone come only while the person is at work; the on-call person and critical phone calls are not affected.
import { Badge, Button, Group, Text, Tooltip } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { WorkStatus } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";

export const useWork = () => useQuery({ queryKey: ["work"], queryFn: () => api<WorkStatus>("/me/work"), refetchInterval: 60_000 });

export function useWorkActions() {
  const client = useQueryClient();
  const done = (status: WorkStatus) => {
    client.setQueryData(["work"], status);
    void client.invalidateQueries({ queryKey: ["work-active"] });
  };
  return {
    start: useMutation({ mutationFn: () => api<WorkStatus>("/me/work/start", { method: "POST" }), onSuccess: done }),
    stop: useMutation({ mutationFn: () => api<WorkStatus>("/me/work/stop", { method: "POST" }), onSuccess: done }),
    setTracking: useMutation({ mutationFn: (tracking: boolean) => api<WorkStatus>("/me/work", { method: "PUT", json: { tracking } }), onSuccess: done }),
  };
}

/** "1:05" from seconds. */
export function hm(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, [enabled]);
  return now;
}

export function WorkButton() {
  const work = useWork();
  const { start, stop } = useWorkActions();
  const status = work.data;
  const now = useNow(Boolean(status?.active));
  if (!status) return null;
  const error = start.error ?? stop.error;

  if (status.active && status.startedAt) {
    return (
      <Group gap={6} wrap="nowrap">
        <Tooltip label={`Since ${new Date(status.startedAt).toLocaleTimeString()}; stops by itself at ${new Date(status.autoStopAt ?? "").toLocaleTimeString()}`}>
          <Badge color="green" variant="light" size="lg">
            at work {hm((now - new Date(status.startedAt).getTime()) / 1000)}
          </Badge>
        </Tooltip>
        <Button size="xs" color="gray" variant="light" onClick={() => stop.mutate()} loading={stop.isPending}>
          Stop
        </Button>
        {error ? <Text size="xs" c="red">{String((error as Error).message)}</Text> : null}
      </Group>
    );
  }
  return (
    <Tooltip
      multiline
      w={260}
      label={
        status.tracking
          ? "You are not at work: email, SMS and phone are paused (alerts stay in the feed; on-call and critical calls still reach you)."
          : "Start tracking your work time. From then on email, SMS and phone come only while you are at work. You can switch this off in My notifications."
      }
    >
      <Button size="xs" color="green" variant={status.tracking ? "filled" : "light"} onClick={() => start.mutate()} loading={start.isPending}>
        Start work
      </Button>
    </Tooltip>
  );
}

/** Section of "My notifications": tracking on / off, today's total, recent sessions. */
export function WorkTimeSection() {
  const work = useWork();
  const { start, stop, setTracking } = useWorkActions();
  const status = work.data;
  if (!status) return <ErrorAlert error={work.error} />;
  return (
    <div>
      <Group justify="space-between" align="start">
        <div>
          <Text fw={500}>Notify me only while I am at work</Text>
          <Text size="xs" c="dimmed" maw={520}>
            Click Start / Stop in the top bar. While you are not at work, alerts stay in the feed but email, SMS and phone are paused. Exceptions: when you are on call, and
            critical phone calls. A session you forget to stop ends after 12 hours.
          </Text>
        </div>
        <Button size="xs" variant={status.tracking ? "light" : "filled"} color={status.tracking ? "gray" : "green"} loading={setTracking.isPending} onClick={() => setTracking.mutate(!status.tracking)}>
          {status.tracking ? "Switch off" : "Switch on"}
        </Button>
      </Group>
      <Group mt="sm" gap="sm">
        <Badge color={status.active ? "green" : "gray"} variant="light">
          {status.active ? "at work" : "not at work"}
        </Badge>
        <Text size="sm">Today: {hm(status.workedTodaySeconds)} h</Text>
        {status.active ? (
          <Button size="xs" variant="light" color="gray" onClick={() => stop.mutate()} loading={stop.isPending}>
            Stop
          </Button>
        ) : (
          <Button size="xs" color="green" onClick={() => start.mutate()} loading={start.isPending}>
            Start work
          </Button>
        )}
      </Group>
      <ErrorAlert error={start.error ?? stop.error ?? setTracking.error} />
      {status.recent.length > 0 && (
        <Text size="xs" c="dimmed" mt="xs">
          Recent:{" "}
          {status.recent
            .slice(0, 5)
            .map((s) => `${new Date(s.startedAt).toLocaleString()} – ${s.endedAt ? new Date(s.endedAt).toLocaleTimeString() : "now"}${s.endedHow === "auto" ? " (auto-stop)" : ""}`)
            .join(" · ")}
        </Text>
      )}
    </div>
  );
}

export const useAtWork = () =>
  useQuery({ queryKey: ["work-active"], queryFn: () => api<{ userId: string; startedAt: string }[]>("/work/active"), refetchInterval: 60_000 });
