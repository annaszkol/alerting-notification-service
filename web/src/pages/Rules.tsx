// Rule list (spec §9 P1). Everyone sees every rule; team rules are changed by the team's managers, personal
// rules by their owner, everything by admins (services/api/routers/rules.py).
import { Anchor, Badge, Button, Card, Group, Loader, SegmentedControl, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useRules } from "../api/directory";
import type { RuleDoc } from "../api/types";
import { ownerLabel, usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { relativeTime } from "../components/time";
import { PageHeader } from "../components/PageHeader";
import { describeCondition } from "../rules/condition";
import { routeSummary } from "../rules/ruleDoc";

export function Rules() {
  const perms = usePermissions();
  const rules = useRules();
  const client = useQueryClient();
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [search, setSearch] = useState("");

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api<RuleDoc>(`/rules/${id}`, { method: "PATCH", json: { enabled } }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["rules"] }),
  });

  const relevant = (r: RuleDoc) =>
    perms.canEditOwner(r.owner) || ("team" in r.owner ? perms.isMember(r.owner.team) : r.owner.user === perms.userId);
  const q = search.trim().toLowerCase();
  const shown = (rules.data ?? [])
    .filter((r) => scope === "all" || relevant(r))
    .filter((r) => !q || r.id.includes(q) || (r.present?.title ?? "").toLowerCase().includes(q) || ownerLabel(r.owner).includes(q));

  return (
    <Stack gap="md">
      <PageHeader
        title="Alert rules"
        description={
          <>
            A rule says which events become alerts and who is told about them: <i>when this happens, notify these people</i>.{" "}
            {perms.isAdmin
          ? "As an admin you can change every rule."
          : perms.managesAnything
            ? "You can change the rules of the teams you manage and your personal rules."
            : "You can create and change personal rules. Team rules are changed by the team’s managers."}
          </>
        }
        actions={
          <Button component={Link} to="/rules/new">
            New rule
          </Button>
        }
      />
      <Group align="end">
        <SegmentedControl
          value={scope}
          onChange={(v) => setScope(v as "mine" | "all")}
          data={[
            { value: "mine", label: "My teams and personal" },
            { value: "all", label: "All rules" },
          ]}
        />
        <TextInput placeholder="Search rules" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={260} />
      </Group>
      <ErrorAlert error={rules.error ?? toggle.error} />
      {rules.isLoading && <Loader />}
      {rules.data && shown.length === 0 && <Text c="dimmed">No rules here yet. Create one with “New rule”.</Text>}
      <Stack gap="sm">
        {shown.map((r) => {
          const editable = perms.canEditOwner(r.owner);
          const off = r.enabled === false;
          return (
            <Card key={r.id} withBorder padding="md" data-rule={r.id} style={{ opacity: off ? 0.6 : 1 }}>
              <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
                <Group align="flex-start" wrap="nowrap" gap="md" style={{ minWidth: 0 }}>
                  <Switch
                    mt={4}
                    aria-label={`Enable ${r.id}`}
                    checked={!off}
                    disabled={!editable || (toggle.isPending && toggle.variables?.id === r.id)}
                    title={editable ? (off ? "Turned off" : "Turned on") : "Only the owner’s manager or an admin can change this"}
                    onChange={(e) => toggle.mutate({ id: r.id, enabled: e.currentTarget.checked })}
                  />
                  <Stack gap={4} style={{ minWidth: 0 }}>
                    <Group gap="xs">
                      <Anchor component={Link} to={`/rules/${r.id}`} fw={600}>
                        {r.id}
                      </Anchor>
                      <Badge variant="light" color={"team" in r.owner ? "indigo" : "grape"} size="sm">
                        {ownerLabel(r.owner)}
                      </Badge>
                      {off && (
                        <Badge variant="outline" color="gray" size="sm">
                          off
                        </Badge>
                      )}
                    </Group>
                    <Text size="sm">
                      <Text span c="dimmed">
                        When{" "}
                      </Text>
                      {describeCondition(r.when)}
                    </Text>
                    <Text size="sm">
                      <Text span c="dimmed">
                        Notify{" "}
                      </Text>
                      {routeSummary(r.route)}
                    </Text>
                    <Text size="xs" c="dimmed">
                      Changed by {r.updatedBy}
                      {r.updatedAt ? `, ${relativeTime(r.updatedAt)}` : ""}
                    </Text>
                  </Stack>
                </Group>
                <Button component={Link} to={`/rules/${r.id}`} size="xs" variant="light">
                  {editable ? "Edit" : "View"}
                </Button>
              </Group>
            </Card>
          );
        })}
      </Stack>
    </Stack>
  );
}
