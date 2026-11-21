// Schema mappings (spec §3, admin), in the order the processor tries them (lower priority number first).
import { Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { MappingDoc } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";

export const useMappings = () => useQuery({ queryKey: ["mappings"], queryFn: () => api<MappingDoc[]>("/mappings") });

export function Mappings() {
  const mappings = useMappings();
  return (
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={2}>Mappings</Title>
        <Group>
          <Button component={Link} to="/unmapped" variant="light">
            Unmapped events
          </Button>
          <Button component={Link} to="/mappings/new">
            New mapping
          </Button>
        </Group>
      </Group>
      <Text size="sm" c="dimmed">
        A mapping recognises an event shape and fills the $-fields rules use. The first matching mapping wins. Changes apply to the next event.
      </Text>
      <ErrorAlert error={mappings.error} />
      {mappings.isLoading && <Loader />}
      {mappings.data?.length === 0 && <Text c="dimmed">No mappings yet.</Text>}
      {!!mappings.data?.length && (
        <Table.ScrollContainer minWidth={640}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Priority</Table.Th>
                <Table.Th>Mapping</Table.Th>
                <Table.Th>Sources</Table.Th>
                <Table.Th>Fills</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {[...mappings.data]
                .sort((a, b) => (a.priority ?? 100) - (b.priority ?? 100) || a.id.localeCompare(b.id))
                .map((m) => (
                  <Table.Tr key={m.id}>
                    <Table.Td>{m.priority ?? 100}</Table.Td>
                    <Table.Td>
                      <Anchor component={Link} to={`/mappings/${m.id}`} fw={600}>
                        {m.id}
                      </Anchor>
                      {m.splitOn && (
                        <Badge size="xs" ml="xs" variant="light">
                          splits {m.splitOn}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{m.sources?.length ? m.sources.join(", ") : <Text span c="dimmed" size="sm">all</Text>}</Table.Td>
                    <Table.Td>
                      <Text size="sm">{Object.keys(m.fields ?? {}).join(", ")}</Text>
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
