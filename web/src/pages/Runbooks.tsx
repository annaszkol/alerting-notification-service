// Runbooks (spec §9 P2, manager): canonical steps, approval and auto mode. Actions run only from approved runbooks.
import { Alert, Badge, Button, Card, Code, Group, Loader, Stack, Switch, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Me, Runbook } from "../api/types";
import { formatTime } from "../components/time";

interface Step {
  id: string;
  kind: string;
  title?: string;
  text?: string;
  action?: string;
  duration?: string;
  approval?: string;
}

export function Runbooks() {
  const client = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const list = useQuery({ queryKey: ["runbooks"], queryFn: () => api<Runbook[]>("/runbooks") });
  const refresh = () => void client.invalidateQueries({ queryKey: ["runbooks"] });
  const approve = useMutation({ mutationFn: (id: string) => api(`/runbooks/${id}/approve`, { method: "POST" }), onSuccess: refresh });
  const mode = useMutation({
    mutationFn: ({ id, auto }: { id: string; auto: boolean }) =>
      api(`/runbooks/${id}`, { method: "PATCH", json: { mode: auto ? "auto" : "manual" } }),
    onSuccess: refresh,
  });
  const manages = (team: string) =>
    Boolean(me.data?.isAdmin || me.data?.teams.some((t) => t.id === team && t.role === "manager"));

  return (
    <Stack>
      <Title order={2}>Runbooks</Title>
      {(approve.error ?? mode.error) && <Alert color="red">{(approve.error ?? mode.error)?.message}</Alert>}
      {list.isLoading && <Loader />}
      {list.data?.length === 0 && <Text c="dimmed">No runbooks yet.</Text>}
      {list.data?.map((rb) => {
        const steps = ((rb.canonical as { steps?: Step[] }).steps ?? []) as Step[];
        return (
          <Card key={rb.id} withBorder>
            <Stack gap="xs">
              <Group justify="space-between">
                <Group gap="xs">
                  <Text fw={600}>{rb.title}</Text>
                  <Badge variant="light" color="gray">
                    {rb.format}
                  </Badge>
                  <Badge variant="outline">{rb.owner.team}</Badge>
                  {rb.approvedBy ? (
                    <Badge color="green" variant="light">
                      approved by {rb.approvedBy}, {formatTime(rb.approvedAt)}
                    </Badge>
                  ) : (
                    <Badge color="yellow">not approved: actions cannot run</Badge>
                  )}
                </Group>
                {manages(rb.owner.team) && (
                  <Group gap="sm">
                    <Switch
                      label="Auto mode"
                      checked={rb.mode === "auto"}
                      onChange={(e) => mode.mutate({ id: rb.id, auto: e.currentTarget.checked })}
                    />
                    {!rb.approvedBy && (
                      <Button size="xs" onClick={() => approve.mutate(rb.id)} loading={approve.isPending}>
                        Approve
                      </Button>
                    )}
                  </Group>
                )}
              </Group>
              <ol style={{ margin: 0, paddingLeft: "1.3rem" }}>
                {steps.map((s) => (
                  <li key={s.id}>
                    <Text size="sm">
                      {s.title ?? s.text ?? (s.kind === "wait" ? `Wait ${s.duration}` : s.action)}{" "}
                      {s.kind === "action" && <Code>{s.action}</Code>}{" "}
                      {s.approval === "required" && (
                        <Badge size="xs" color="grape" variant="light">
                          approval required
                        </Badge>
                      )}
                    </Text>
                  </li>
                ))}
              </ol>
              <details>
                <summary>
                  <Text span size="sm" c="dimmed">
                    Original ({rb.format})
                  </Text>
                </summary>
                <Code block>{rb.original}</Code>
              </details>
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}
