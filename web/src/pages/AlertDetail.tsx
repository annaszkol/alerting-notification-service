// P1: alert details (spec §9): fields from the rule, count, timeline, notifications and their status,
// runbooks with values, acknowledge / resolve. Refetches when the WebSocket reports a change of this alert.
import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, Textarea, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, queryString } from "../api/client";
import type { AlertDetail as Detail, Approval, Suggestion, TimelineEntry } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { JsonBlock } from "../components/JsonTree";
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
  const perms = usePermissions();
  // approvals this person may decide: the API's own answer (role in the approval's team), not a guess
  const mine = useQuery({
    queryKey: ["approvals", "mine"],
    queryFn: () => api<Approval[]>(`/approvals${queryString({ status: "pending", mine: "true" })}`),
  });

  useEffect(
    () =>
      live.listen((message) => {
        if ((message.type === "alert.upsert" && message.alert.id === id) || message.type === "resync") {
          void client.invalidateQueries({ queryKey: ["alert", id] });
          void client.invalidateQueries({ queryKey: ["timeline", id] });
          void client.invalidateQueries({ queryKey: ["run"] });
          void client.invalidateQueries({ queryKey: ["approvals"] });
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
  const canRun = a.teams.some((t) => perms.isMember(t));
  const decidable = new Set((mine.data ?? []).map((x) => x.id));

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
      <ErrorAlert error={actionError} />

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
              <Table.Tr>
                <Table.Td c="dimmed" w="35%">
                  Rule
                </Table.Td>
                <Table.Td>
                  {a.ruleId ? (
                    <Group gap="xs">
                      <Anchor component={Link} to={`/rules/${a.ruleId}`}>
                        {a.ruleId}
                      </Anchor>
                      <Anchor component={Link} to={`/rules/${a.ruleId}?alert=${a.id}`} size="xs">
                        test with this alert
                      </Anchor>
                    </Group>
                  ) : (
                    "deleted"
                  )}
                </Table.Td>
              </Table.Tr>
              <Row label="First seen" value={formatTime(a.firstSeen)} />
              <Row label="Last seen" value={formatTime(a.lastSeen)} />
              {a.ackedBy && <Row label="Acknowledged" value={`${a.ackedBy}, ${formatTime(a.ackedAt)}`} />}
              {a.resolvedAt && (
                <Row label="Resolved" value={`${a.resolvedHow ?? ""}${a.resolvedBy ? ` by ${a.resolvedBy}` : ""}, ${formatTime(a.resolvedAt)}`} />
              )}
              {a.resolveNote && <Row label="Resolve note" value={a.resolveNote} />}
              {a.autoResolveAfter && <Row label="Auto-resolves after" value={`${a.autoResolveAfter} without events`} />}
              {a.correlationKey && <Row label="Correlation key" value={a.correlationKey} mono />}
              {a.previousAlertId && (
                <Table.Tr>
                  <Table.Td c="dimmed">Previous alert</Table.Td>
                  <Table.Td>
                    <Anchor component={Link} to={`/alerts/${a.previousAlertId}`}>
                      alert {a.previousAlertId}
                    </Anchor>
                  </Table.Td>
                </Table.Tr>
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
          {a.runbooks.length > 0 && !canRun && (
            <Text size="xs" c="dimmed" mb="xs">
              Only members of {a.teams.join(", ")} can run these steps.
            </Text>
          )}
          <Stack gap="md">
            {a.runbooks.map((rb) => (
              <RunbookPanel key={rb.id} alertId={a.id} runbook={rb} canRun={canRun} decidable={decidable} />
            ))}
          </Stack>
        </Card>
      </SimpleGrid>

      <ProposeChange alert={a} />

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

      <Card withBorder>
        <Title order={4} mb="xs">
          Events
        </Title>
        <details>
          <summary>
            <Text span size="sm">
              First event
            </Text>
          </summary>
          <JsonBlock value={a.firstEvent} />
        </details>
        {a.count > 1 && (
          <details>
            <summary>
              <Text span size="sm">
                Latest event
              </Text>
            </summary>
            <JsonBlock value={a.lastEvent} />
          </details>
        )}
      </Card>
    </Stack>
  );
}

/** "Propose fixes" (spec §9): anyone can propose a change of the rule or a runbook; managers decide. */
function ProposeChange({ alert }: { alert: Detail }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const runbookIds = alert.runbooks.map((r) => r.id);
  const [target, setTarget] = useState<"rule" | "runbook">(alert.ruleId ? "rule" : "runbook");
  const [runbook, setRunbook] = useState<string | null>(runbookIds[0] ?? null);
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () =>
      api<Suggestion>(`/alerts/${alert.id}/suggestions`, {
        json: { target, targetId: target === "rule" ? alert.ruleId : runbook, text: text.trim() },
      }),
    onSuccess: () => {
      setText("");
      setOpen(false);
      void client.invalidateQueries({ queryKey: ["timeline", alert.id] });
      void client.invalidateQueries({ queryKey: ["suggestions"] });
    },
  });
  const targets = [
    ...(alert.ruleId ? [{ value: "rule", label: "the rule" }] : []),
    ...(runbookIds.length ? [{ value: "runbook", label: "a runbook" }] : []),
  ];
  if (targets.length === 0) return null;

  return (
    <Card withBorder>
      <Group justify="space-between">
        <Title order={4}>Propose a change</Title>
        {!open && (
          <Button size="xs" variant="light" onClick={() => setOpen(true)}>
            Propose a change
          </Button>
        )}
      </Group>
      {send.isSuccess && !open && (
        <Text size="sm" c="green" mt={4}>
          Sent. The team’s managers see it under “Proposed changes”.
        </Text>
      )}
      {open && (
        <Stack gap="xs" mt="xs">
          <Group align="end">
            {targets.length > 1 && <SegmentedControl value={target} onChange={(v) => setTarget(v as "rule" | "runbook")} data={targets} />}
            {target === "rule" ? (
              <Text size="sm">
                Rule <code>{alert.ruleId}</code>
              </Text>
            ) : (
              <Select data={alert.runbooks.map((r) => ({ value: r.id, label: r.title }))} value={runbook} onChange={setRunbook} allowDeselect={false} w={260} />
            )}
          </Group>
          <Textarea
            placeholder="What should change and why? e.g. “Raise the threshold to 20M — 15M rejections are normal at month end.”"
            autosize
            minRows={3}
            maxLength={5000}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
          />
          <ErrorAlert error={send.error} />
          <Group>
            <Button size="xs" onClick={() => send.mutate()} loading={send.isPending} disabled={!text.trim() || (target === "runbook" && !runbook)}>
              Send proposal
            </Button>
            <Button size="xs" variant="default" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </Group>
        </Stack>
      )}
    </Card>
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
