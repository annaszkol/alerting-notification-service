// Rule list (spec §9 P1). Everyone sees every rule; team rules are changed by the team's managers, personal
// rules by their owner, everything by admins (services/api/routers/rules.py).
import { Anchor, Badge, Button, Group, Loader, SegmentedControl, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useRules } from "../api/directory";
import type { RuleDoc } from "../api/types";
import { ownerLabel, usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { relativeTime } from "../components/time";
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
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={2}>Rules</Title>
        <Button component={Link} to="/rules/new">
          New rule
        </Button>
      </Group>
      <Text size="sm" c="dimmed">
        {perms.isAdmin
          ? "As an admin you can change every rule."
          : perms.managesAnything
            ? "You can change the rules of the teams you manage and your personal rules."
            : "You can create and change personal rules. Team rules are changed by the team’s managers."}
      </Text>
      <Group align="end">
        <SegmentedControl
          value={scope}
          onChange={(v) => setScope(v as "mine" | "all")}
          data={[
            { value: "mine", label: "My teams and personal" },
            { value: "all", label: "All rules" },
          ]}
        />
        <TextInput placeholder="Search id, title, owner" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={260} />
      </Group>
      <ErrorAlert error={rules.error ?? toggle.error} />
      {rules.isLoading && <Loader />}
      {rules.data && shown.length === 0 && <Text c="dimmed">No rules here yet. Create one with “New rule”.</Text>}
      {shown.length > 0 && (
        <Table.ScrollContainer minWidth={760}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>On</Table.Th>
                <Table.Th>Rule</Table.Th>
                <Table.Th>Owner</Table.Th>
                <Table.Th>Notifies</Table.Th>
                <Table.Th>Changed</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((r) => {
                const editable = perms.canEditOwner(r.owner);
                return (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      <Switch
                        aria-label={`Enable ${r.id}`}
                        checked={r.enabled !== false}
                        disabled={!editable || (toggle.isPending && toggle.variables?.id === r.id)}
                        title={editable ? undefined : "Only the owner’s manager or an admin can change this"}
                        onChange={(e) => toggle.mutate({ id: r.id, enabled: e.currentTarget.checked })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Anchor component={Link} to={`/rules/${r.id}`} fw={600}>
                        {r.id}
                      </Anchor>
                      {r.present?.title && (
                        <Text size="xs" c="dimmed" lineClamp={1}>
                          {r.present.title}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="outline" color={"team" in r.owner ? "blue" : "grape"}>
                        {ownerLabel(r.owner)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{routeSummary(r.route)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" title={r.updatedAt}>
                        {r.updatedBy}
                        {r.updatedAt ? `, ${relativeTime(r.updatedAt)}` : ""}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Button component={Link} to={`/rules/${r.id}`} size="xs" variant="light">
                        {editable ? "Edit" : "View"}
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}
