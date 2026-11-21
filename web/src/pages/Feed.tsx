// P1: live alert feed with filters (spec §9). REST page + WebSocket upserts (useAlertsFeed).
import { Alert, Anchor, Badge, Button, Group, Loader, MultiSelect, Select, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { SEVERITIES, STATES, type AlertState, type Me, type Severity } from "../api/types";
import { SeverityBadge, StateBadge } from "../components/badges";
import { relativeTime } from "../components/time";
import { DEFAULT_STATES } from "../feed/feedState";
import { useAlertsFeed } from "../feed/useAlertsFeed";

export function Feed() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const [team, setTeam] = useState<string | null>(null);
  const [severity, setSeverity] = useState<Severity[]>([]);
  const [state, setState] = useState<AlertState[]>(DEFAULT_STATES);
  const [search, setSearch] = useState("");
  const [q] = useDebouncedValue(search, 300);
  const feed = useAlertsFeed({ team, severity, state, q });

  const teams = me.data?.teams.map((t) => ({ value: t.id, label: t.name })) ?? [];

  return (
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={2}>Alerts</Title>
        <Text size="sm" c="dimmed">
          {feed.alerts.length} shown
        </Text>
      </Group>
      <Group align="end" wrap="wrap">
        <Select label="Team" placeholder="All my alerts" data={teams} value={team} onChange={setTeam} clearable w={200} />
        <MultiSelect
          label="Severity"
          placeholder="Any"
          data={SEVERITIES}
          value={severity}
          onChange={(v) => setSeverity(v as Severity[])}
          w={240}
        />
        <MultiSelect
          label="State"
          data={STATES}
          value={state}
          onChange={(v) => setState(v as AlertState[])}
          w={260}
        />
        <TextInput label="Title contains" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={220} />
      </Group>

      {feed.error && <Alert color="red">Cannot load alerts: {feed.error}</Alert>}
      {feed.loading && feed.alerts.length === 0 ? (
        <Loader />
      ) : feed.alerts.length === 0 ? (
        <Text c="dimmed">No alerts match these filters. New ones appear here as they happen.</Text>
      ) : (
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Severity</Table.Th>
                <Table.Th>Title</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th ta="right">Count</Table.Th>
                <Table.Th>Teams</Table.Th>
                <Table.Th>Last seen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {feed.alerts.map((alert) => (
                <Table.Tr key={alert.id}>
                  <Table.Td>
                    <SeverityBadge severity={alert.severity} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Anchor component={Link} to={`/alerts/${alert.id}`}>
                        {alert.title}
                      </Anchor>
                      {alert.hasDeadNotification && (
                        <Badge color="red" variant="outline" size="xs" title="A notification could not be delivered">
                          undelivered
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <StateBadge state={alert.state} />
                  </Table.Td>
                  <Table.Td ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {alert.count}
                  </Table.Td>
                  <Table.Td>{alert.teams.join(", ")}</Table.Td>
                  <Table.Td title={alert.lastSeen}>{relativeTime(alert.lastSeen)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {feed.hasMore && (
        <Button variant="default" onClick={() => void feed.loadMore()}>
          Load more
        </Button>
      )}
    </Stack>
  );
}
