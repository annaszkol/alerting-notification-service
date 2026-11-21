// "Propose fixes" (spec §9 P3): changes proposed from alerts to rules and runbooks. A manager of the team that owns
// the rule or runbook (or an admin) accepts or rejects; accepting is a decision, the change itself is made in the
// rule or runbook editor (linked).
import { Anchor, Badge, Button, Card, Group, Loader, SegmentedControl, Stack, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useRules, useRunbooks } from "../api/directory";
import type { Suggestion } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { relativeTime } from "../components/time";

type Status = Suggestion["status"];
const STATUS_COLOR: Record<Status, string> = { open: "blue", accepted: "green", rejected: "gray" };

export function Suggestions() {
  const perms = usePermissions();
  const rules = useRules();
  const runbooks = useRunbooks();
  const client = useQueryClient();
  const [status, setStatus] = useState<Status | "all">("open");
  const [scopeChoice, setScope] = useState<"decide" | "all" | null>(null);
  const scope = scopeChoice ?? (perms.managesAnything ? "decide" : "all");
  const list = useQuery({ queryKey: ["suggestions"], queryFn: () => api<Suggestion[]>("/suggestions") });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: "accept" | "reject" }) => api<Suggestion>(`/suggestions/${id}/${decision}`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["suggestions"] }),
  });

  /** team owning the target; null for a personal rule or a deleted target (only admins decide those) */
  const targetTeam = (s: Suggestion): string | null => {
    if (s.target === "rule") {
      const owner = rules.data?.find((r) => r.id === s.targetId)?.owner;
      return owner && "team" in owner ? owner.team : null;
    }
    return runbooks.data?.find((r) => r.id === s.targetId)?.owner.team ?? null;
  };
  const canDecide = (s: Suggestion) => {
    const team = targetTeam(s);
    return perms.isAdmin || (team !== null && perms.isManager(team));
  };

  const shown = (list.data ?? []).filter((s) => (status === "all" || s.status === status) && (scope === "all" || canDecide(s)));

  return (
    <Stack>
      <Title order={2}>Proposed changes</Title>
      <Text size="sm" c="dimmed">
        Anyone can propose a change from an alert page (“Propose a change”). Managers of the owning team decide.
      </Text>
      <Group>
        <SegmentedControl
          value={status}
          onChange={(v) => setStatus(v as Status | "all")}
          data={[
            { value: "open", label: "Open" },
            { value: "accepted", label: "Accepted" },
            { value: "rejected", label: "Rejected" },
            { value: "all", label: "All" },
          ]}
        />
        {perms.managesAnything && (
          <SegmentedControl
            value={scope}
            onChange={(v) => setScope(v as "decide" | "all")}
            data={[
              { value: "decide", label: "I decide" },
              { value: "all", label: "Everything" },
            ]}
          />
        )}
      </Group>
      <ErrorAlert error={list.error ?? decide.error} />
      {list.isLoading && <Loader />}
      {list.data && shown.length === 0 && <Text c="dimmed">Nothing here.</Text>}
      {shown.map((s) => (
        <Card key={s.id} withBorder>
          <Stack gap={6}>
            <Group justify="space-between" wrap="wrap">
              <Group gap="xs">
                <Badge color={STATUS_COLOR[s.status]}>{s.status}</Badge>
                <Text size="sm">
                  {s.target}{" "}
                  <Anchor component={Link} to={s.target === "rule" ? `/rules/${s.targetId}` : "/runbooks"} fw={600}>
                    {s.targetId}
                  </Anchor>{" "}
                  · from{" "}
                  <Anchor component={Link} to={`/alerts/${s.alertId}`}>
                    alert {s.alertId}
                  </Anchor>
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                {s.author}, {relativeTime(s.createdAt)}
                {s.decidedBy && ` · ${s.status} by ${s.decidedBy}`}
              </Text>
            </Group>
            <Text style={{ whiteSpace: "pre-wrap" }}>{s.text}</Text>
            {s.status === "open" && canDecide(s) && (
              <Group gap="xs">
                <Button size="xs" color="green" onClick={() => decide.mutate({ id: s.id, decision: "accept" })} loading={decide.isPending && decide.variables?.id === s.id && decide.variables.decision === "accept"}>
                  Accept
                </Button>
                <Button size="xs" variant="light" color="red" onClick={() => decide.mutate({ id: s.id, decision: "reject" })} loading={decide.isPending && decide.variables?.id === s.id && decide.variables.decision === "reject"}>
                  Reject
                </Button>
                <Button size="xs" variant="subtle" component={Link} to={s.target === "rule" ? `/rules/${s.targetId}?alert=${s.alertId}` : `/runbooks/${s.targetId}/edit`}>
                  Open {s.target} to change it
                </Button>
              </Group>
            )}
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}
