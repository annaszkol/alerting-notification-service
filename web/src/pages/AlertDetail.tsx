// P1: alert details (spec §9): fields from the rule, count, timeline, notifications and their status,
// runbooks with values, acknowledge / resolve. Refetches when the WebSocket reports a change of this alert.
import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, SimpleGrid, Stack, Table, Text, Textarea, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { AlertDetail as Detail, TimelineEntry } from "../api/types";
import { SeverityBadge, StateBadge } from "../components/badges";
import { formatTime } from "../components/time";
import { useLive } from "../feed/live";
import { RunbookPanel } from "../runbooks/RunbookPanel";

const TIMELINE_COLOR: Record<TimelineEntry["kind"], string> = {
  state: "blue",
  notification: "gray",
  step: "teal",
  approval: "grape",
  suggestion: "yellow",
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function AlertDetail() {
  const id = Number(useParams().id);
  const live = useLive();
  const client = useQueryClient();
  const alert = useQuery({ queryKey: ["alert", id], queryFn: () => api<Detail>(`/alerts/${id}`) });
  const timeline = useQuery({ queryKey: ["timeline", id], queryFn: () => api<TimelineEntry[]>(`/alerts/${id}/timeline`) });
  const [note, setNote] = useState("");

  useEffect(
    () =>
      live.listen((message) => {
        if ((message.type === "alert.upsert" && message.alert.id === id) || message.type === "resync") {
          void client.invalidateQueries({ queryKey: ["alert", id] });
          void client.invalidateQueries({ queryKey: ["timeline", id] });
          void client.invalidateQueries({ queryKey: ["run"] });
        }
      }),
    [live, client, id],
  );

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["alert", id] });
    void client.invalidateQueries({ queryKey: ["timeline", id] });
  };
  const ack = useMutation({ mutationFn: () => api(`/alerts/${id}/ack`, { method: "POST" }), onSuccess: refresh });
  const resolve = useMutation({
    mutationFn: () => api(`/alerts/${id}/resolve`, { json: note.trim() ? { note: note.trim() } : {} }),
    onSuccess: () => {
      setNote("");
      refresh();
    },
  });

  if (alert.isLoading) return <Loader />;
  if (alert.error || !alert.data) return <Alert color="red">Cannot load alert {id}: {alert.error?.message}</Alert>;
  const a = alert.data;
  const actionError = ack.error ?? resolve.error;

  return (
    <Stack>
      <Anchor component={Link} to="/" size="sm">
        ← All alerts
      </Anchor>
      <Group gap="sm">
        <SeverityBadge severity={a.severity} />
        <StateBadge state={a.state} />
        <Badge variant="outline" color="gray">
          ×{a.count}
        </Badge>
      </Group>
      <Title order={2} style={{ overflowWrap: "anywhere" }}>
        {a.title}
      </Title>

      {a.hasDeadNotification && (
        <Alert color="red" variant="filled" title="A notification could not be delivered">
          At least one notification for this alert gave up after all retries. Check the notifications below.
        </Alert>
      )}
      {actionError && <Alert color="red">{actionError.message}</Alert>}

      {a.state !== "resolved" && (
        <Card withBorder>
          <Group align="end" wrap="wrap">
            {a.state === "open" && (
              <Button onClick={() => ack.mutate()} loading={ack.isPending}>
                Acknowledge
              </Button>
            )}
            <Textarea
              label="Resolve note (optional)"
              value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
              autosize
              minRows={1}
              style={{ flex: "1 1 240px" }}
            />
            <Button color="green" variant="light" onClick={() => resolve.mutate()} loading={resolve.isPending}>
              Resolve
            </Button>
          </Group>
        </Card>
      )}

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card withBorder>
          <Title order={4} mb="xs">
            Details
          </Title>
          <Table>
            <Table.Tbody>
              <Row label="Teams" value={a.teams.join(", ")} />
              <Row label="Rule" value={a.ruleId ?? "deleted"} />
              <Row label="First seen" value={formatTime(a.firstSeen)} />
              <Row label="Last seen" value={formatTime(a.lastSeen)} />
              {a.ackedBy && <Row label="Acknowledged" value={`${a.ackedBy}, ${formatTime(a.ackedAt)}`} />}
              {a.resolvedAt && (
                <Row label="Resolved" value={`${a.resolvedHow ?? ""} ${a.resolvedBy ?? ""}, ${formatTime(a.resolvedAt)}`} />
              )}
              {a.fields.map((f) => (
                <Row key={f.path} label={f.path} value={show(f.value)} mono />
              ))}
            </Table.Tbody>
          </Table>
          {a.body && (
            <Text mt="sm" style={{ whiteSpace: "pre-wrap" }}>
              {a.body}
            </Text>
          )}
        </Card>

        <Card withBorder>
          <Title order={4} mb="xs">
            Runbooks
          </Title>
          {a.runbooks.length === 0 && <Text c="dimmed">No runbook matches this alert.</Text>}
          <Stack gap="md">
            {a.runbooks.map((rb) => (
              <RunbookPanel key={rb.id} alertId={a.id} runbook={rb} />
            ))}
          </Stack>
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Title order={4} mb="xs">
          Notifications
        </Title>
        {a.notifications.length === 0 ? (
          <Text c="dimmed">No notifications (UI only).</Text>
        ) : (
          <Table.ScrollContainer minWidth={560}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>To</Table.Th>
                  <Table.Th>Channel</Table.Th>
                  <Table.Th>Kind</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th ta="right">Attempts</Table.Th>
                  <Table.Th>Last error</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {a.notifications.map((n) => (
                  <Table.Tr key={n.id}>
                    <Table.Td>{n.userId}</Table.Td>
                    <Table.Td>{n.channel}</Table.Td>
                    <Table.Td>{n.kind}</Table.Td>
                    <Table.Td>
                      <Badge color={n.status === "dead" ? "red" : n.status === "sent" ? "green" : "gray"} variant="light">
                        {n.status}
                      </Badge>
                    </Table.Td>
                    <Table.Td ta="right">{n.attempts}</Table.Td>
                    <Table.Td>{n.lastError ?? ""}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">
          Timeline
        </Title>
        {timeline.isLoading && <Loader size="sm" />}
        <Stack gap={6}>
          {timeline.data?.map((entry, i) => (
            <Group key={i} gap="sm" wrap="nowrap" align="start">
              <Text size="xs" c="dimmed" w={150} style={{ flexShrink: 0 }}>
                {formatTime(entry.ts)}
              </Text>
              <Badge size="xs" color={TIMELINE_COLOR[entry.kind]} variant="light" style={{ flexShrink: 0 }}>
                {entry.kind}
              </Badge>
              <Text size="sm">{entry.summary}</Text>
            </Group>
          ))}
        </Stack>
      </Card>
    </Stack>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <Table.Tr>
      <Table.Td c="dimmed" w="35%">
        {label}
      </Table.Td>
      <Table.Td style={{ overflowWrap: "anywhere" }}>{mono ? <Code>{value}</Code> : value}</Table.Td>
    </Table.Tr>
  );
}
