// A runbook on the alert page: steps rendered with the alert's values, and its latest run with buttons
// (spec §7, §9 P2: start, run a step, tick a manual step, approve or reject).
import { Alert, Anchor, Badge, Button, Group, Stack, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AlertRunbook, RunbookRun } from "../api/types";
import { STEP_STATUS_COLOR, stepActions, stepWho, type StepAction } from "./stepActions";

const RUN_COLOR: Record<RunbookRun["status"], string> = {
  running: "blue",
  waiting_approval: "grape",
  succeeded: "green",
  failed: "red",
  rejected: "red",
  cancelled: "gray",
};

interface Props {
  alertId: number;
  runbook: AlertRunbook;
  /** member of one of the alert's teams: may start runs and steps and tick manual steps (API: _require_alert_member) */
  canRun: boolean;
  /** approval ids this person may decide (GET /approvals?mine=true) */
  decidable: Set<number>;
}

export function RunbookPanel({ alertId, runbook, canRun, decidable }: Props) {
  const client = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runbook.runId],
    queryFn: () => api<RunbookRun>(`/runbook-runs/${runbook.runId}`),
    enabled: Boolean(runbook.runId),
    refetchInterval: (q) => (q.state.data && ["running", "waiting_approval"].includes(q.state.data.status) ? 3000 : false),
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["alert", alertId] });
    void client.invalidateQueries({ queryKey: ["timeline", alertId] });
    void client.invalidateQueries({ queryKey: ["run", runbook.runId] });
  };

  const start = useMutation({
    mutationFn: () => api<RunbookRun>(`/alerts/${alertId}/runbook-runs`, { json: { runbookId: runbook.id } }),
    onSuccess: refresh,
  });
  const act = useMutation({
    mutationFn: ({ action, stepId, approvalId }: { action: StepAction; stepId: string; approvalId?: number | null }) => {
      const runId = run.data?.id;
      if (action === "done") return api(`/runbook-runs/${runId}/steps/${stepId}/done`, { json: {} });
      if (action === "run") return api(`/runbook-runs/${runId}/steps/${stepId}/start`, { method: "POST" });
      return api(`/approvals/${approvalId}/${action}`, { method: "POST" });
    },
    onSuccess: refresh,
  });

  const active = run.data && ["running", "waiting_approval"].includes(run.data.status);
  const stepRuns = new Map(run.data?.steps.map((s) => [s.stepId, s]));
  const error = start.error ?? act.error;

  return (
    <Stack gap={6}>
      <Group gap="xs" justify="space-between">
        <Group gap="xs">
          <Text fw={600}>{runbook.title}</Text>
          <Badge variant="light" color="gray">
            {runbook.format}
          </Badge>
          {runbook.mode === "auto" && <Badge variant="light">auto</Badge>}
          {!runbook.approved && <Badge color="gray">not approved: read only</Badge>}
          {run.data && <Badge color={RUN_COLOR[run.data.status]}>{run.data.status.replace("_", " ")}</Badge>}
        </Group>
        {runbook.approved && !active && canRun && (
          <Button size="xs" onClick={() => start.mutate()} loading={start.isPending}>
            {run.data ? "Run again" : "Start run"}
          </Button>
        )}
      </Group>
      {runbook.url && (
        <Anchor href={runbook.url} target="_blank" rel="noreferrer" size="sm">
          {runbook.url}
        </Anchor>
      )}
      {error && <Alert color="red">{error.message}</Alert>}
      <ol style={{ margin: 0, paddingLeft: "1.3rem", display: "grid", gap: 6 }}>
        {runbook.steps.map((step) => {
          const sr = stepRuns.get(step.id);
          const reached = sr && sr.id !== null && sr.id !== undefined;
          return (
            <li key={step.id}>
              <Group gap={6} wrap="wrap">
                <Text size="sm">{step.kind === "text" ? (step.text ?? step.title) : step.title}</Text>
                {step.effectiveApproval === "required" && (
                  <Badge size="xs" color="grape" variant="light">
                    needs {step.approvalRole ?? "manager"} approval
                  </Badge>
                )}
                {reached && (
                  <Badge size="xs" color={STEP_STATUS_COLOR[sr.status]}>
                    {sr.status.replace("_", " ")}
                  </Badge>
                )}
                {stepActions(step, sr)
                  .filter((action) => (action === "approve" || action === "reject" ? Boolean(sr?.approvalId && decidable.has(sr.approvalId)) : canRun))
                  .map((action) => (
                  <Button
                    key={action}
                    size="compact-xs"
                    variant={action === "reject" ? "light" : "filled"}
                    color={action === "reject" ? "red" : action === "approve" ? "grape" : undefined}
                    loading={act.isPending && act.variables?.stepId === step.id && act.variables.action === action}
                    onClick={() => act.mutate({ action, stepId: step.id, approvalId: sr?.approvalId })}
                  >
                    {{ done: "Done", run: "Run", approve: "Approve", reject: "Reject" }[action]}
                  </Button>
                  ))}
                {sr?.status === "waiting_approval" && !(sr.approvalId && decidable.has(sr.approvalId)) && (
                  <Text size="xs" c="dimmed">
                    waiting for a {step.approvalRole ?? "manager"} to approve
                  </Text>
                )}
              </Group>
              {sr && (stepWho(sr) || sr.result?.error || sr.note) && (
                <Text size="xs" c="dimmed">
                  {[stepWho(sr), sr.note, sr.result?.error ? `error: ${sr.result.error}` : ""].filter(Boolean).join(" · ")}
                </Text>
              )}
            </li>
          );
        })}
      </ol>
    </Stack>
  );
}
