// Approvals waiting for me (spec §7: a step that needs approval waits for someone with the required role).
import { Anchor, Badge, Button, Group, Loader, SegmentedControl, Stack, Table, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, queryString } from "../api/client";
import type { Approval } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";
import { relativeTime } from "../components/time";

export function Approvals() {
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const client = useQueryClient();
  const list = useQuery({
    queryKey: ["approvals", scope],
    queryFn: () =>
      api<Approval[]>(`/approvals${queryString({ status: "pending", mine: scope === "mine" ? "true" : null })}`),
    refetchInterval: 5000,
  });
  // "All pending" also lists approvals that need someone else's role; only mine get buttons
  const mine = useQuery({
    queryKey: ["approvals", "mine"],
    queryFn: () => api<Approval[]>(`/approvals${queryString({ status: "pending", mine: "true" })}`),
    refetchInterval: 5000,
  });
  const decidable = new Set((mine.data ?? []).map((a) => a.id));
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: "approve" | "reject" }) =>
      api<Approval>(`/approvals/${id}/${decision}`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["approvals"] }),
  });

  return (
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={2}>Approvals</Title>
        <SegmentedControl
          value={scope}
          onChange={(v) => setScope(v as "mine" | "all")}
          data={[
            { value: "mine", label: "I can decide" },
            { value: "all", label: "All pending" },
          ]}
        />
      </Group>
      <ErrorAlert error={decide.error ?? list.error} />
      {list.isLoading && <Loader />}
      {list.data?.length === 0 && <Text c="dimmed">{scope === "mine" ? "Nothing waits for your approval." : "Nothing waits for approval."}</Text>}
      {!!list.data?.length && (
        <Table.ScrollContainer minWidth={640}>
          <Table verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Step</Table.Th>
                <Table.Th>Alert</Table.Th>
                <Table.Th>Needs</Table.Th>
                <Table.Th>Requested</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.data.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td>{a.stepTitle ?? a.stepId}</Table.Td>
                  <Table.Td>
                    <Anchor component={Link} to={`/alerts/${a.alertId}`}>
                      alert {a.alertId}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="grape">
                      {a.team} {a.requiredRole}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{relativeTime(a.createdAt)}</Table.Td>
                  <Table.Td>
                    {decidable.has(a.id) ? (
                      <Group gap="xs" justify="end" wrap="nowrap">
                        <Button size="xs" color="grape" loading={decide.isPending && decide.variables?.id === a.id && decide.variables.decision === "approve"} onClick={() => decide.mutate({ id: a.id, decision: "approve" })}>
                          Approve
                        </Button>
                        <Button size="xs" variant="light" color="red" loading={decide.isPending && decide.variables?.id === a.id && decide.variables.decision === "reject"} onClick={() => decide.mutate({ id: a.id, decision: "reject" })}>
                          Reject
                        </Button>
                      </Group>
                    ) : (
                      <Text size="xs" c="dimmed" ta="right">
                        needs a {a.requiredRole} of {a.team}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}
