// Runbooks (spec §7, §9 P2). Everyone reads them; managers of the owner team import, edit, approve, switch auto
// mode and delete. Actions run only from approved runbooks.
import { Alert, Badge, Button, Card, Code, Group, Loader, Select, Stack, Switch, Text, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useRunbooks } from "../api/directory";
import type { CanonicalStep } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ConfirmButton } from "../components/ConfirmButton";
import { ErrorAlert } from "../components/ErrorAlert";
import { formatTime } from "../components/time";
import { effectiveApproval, stepLabel } from "../runbooks/steps";
import { useActions } from "./RunbookEditor";

export function Runbooks() {
  const client = useQueryClient();
  const perms = usePermissions();
  const list = useRunbooks();
  const actions = useActions();
  const [team, setTeam] = useState<string | null>(null);
  const refresh = () => void client.invalidateQueries({ queryKey: ["runbooks"] });
  const approve = useMutation({ mutationFn: (id: string) => api(`/runbooks/${id}/approve`, { method: "POST" }), onSuccess: refresh });
  const mode = useMutation({
    mutationFn: ({ id, auto }: { id: string; auto: boolean }) => api(`/runbooks/${id}`, { method: "PATCH", json: { mode: auto ? "auto" : "manual" } }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/runbooks/${id}`, { method: "DELETE" }), onSuccess: refresh });

  const teams = [...new Set((list.data ?? []).map((r) => r.owner.team))].sort();
  const shown = (list.data ?? []).filter((r) => !team || r.owner.team === team);

  return (
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={2}>Runbooks</Title>
        <Group align="end">
          <Select placeholder="All teams" data={teams} value={team} onChange={setTeam} clearable w={180} />
          {perms.managesAnything && (
            <Button component={Link} to="/runbooks/import">
              Import runbook
            </Button>
          )}
        </Group>
      </Group>
      <ErrorAlert error={list.error ?? approve.error ?? mode.error ?? remove.error} />
      {list.isLoading && <Loader />}
      {list.data && shown.length === 0 && <Text c="dimmed">No runbooks yet.</Text>}
      {shown.map((rb) => {
        const canonical = rb.canonical as { steps?: CanonicalStep[]; link?: string; match?: unknown };
        const steps = canonical.steps ?? [];
        const manager = perms.isManager(rb.owner.team);
        const review = steps.filter((s) => s.needs_review).length;
        return (
          <Card key={rb.id} withBorder>
            <Stack gap="xs">
              <Group justify="space-between" wrap="wrap">
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
                  {rb.mode === "auto" && <Badge variant="light">auto</Badge>}
                </Group>
                {manager && (
                  <Group gap="sm">
                    <Switch
                      label="Auto mode"
                      title="Steps that need no approval start by themselves when a matching alert opens"
                      checked={rb.mode === "auto"}
                      disabled={mode.isPending}
                      onChange={(e) => mode.mutate({ id: rb.id, auto: e.currentTarget.checked })}
                    />
                    {!rb.approvedBy && (
                      <Button size="xs" onClick={() => approve.mutate(rb.id)} loading={approve.isPending && approve.variables === rb.id}>
                        Approve
                      </Button>
                    )}
                    <Button size="xs" variant="light" component={Link} to={`/runbooks/${rb.id}/edit`}>
                      Edit steps
                    </Button>
                    <ConfirmButton size="xs" title={`Delete runbook ${rb.title}?`} message="Rules that link it will no longer show it. This cannot be undone." onConfirm={() => remove.mutate(rb.id)}>
                      Delete
                    </ConfirmButton>
                  </Group>
                )}
              </Group>
              {review > 0 && (
                <Alert color="yellow" p="xs">
                  {review} step{review === 1 ? "" : "s"} the importer did not fully understand — {manager ? "check them with “Edit steps” before approving." : "a manager should check them."}
                </Alert>
              )}
              {canonical.link && (
                <a href={canonical.link} target="_blank" rel="noreferrer">
                  {canonical.link}
                </a>
              )}
              {steps.length > 0 && (
                <ol style={{ margin: 0, paddingLeft: "1.3rem" }}>
                  {steps.map((s) => (
                    <li key={s.id}>
                      <Group gap={6}>
                        <Text size="sm">{stepLabel(s)}</Text>
                        {s.kind === "action" && <Code>{s.action}</Code>}
                        {effectiveApproval(s, actions.data ?? []) === "required" && (
                          <Badge size="xs" color="grape" variant="light">
                            needs {s.approval_role ?? "manager"} approval
                          </Badge>
                        )}
                        {s.needs_review && (
                          <Badge size="xs" color="yellow">
                            review
                          </Badge>
                        )}
                      </Group>
                    </li>
                  ))}
                </ol>
              )}
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
