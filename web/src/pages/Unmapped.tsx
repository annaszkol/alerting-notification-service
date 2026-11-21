// "Niezmapowane" (spec §3 step 4, §9 P2, admin): events no mapping recognised, grouped by their set of keys.
// From here: map the shape (mapping editor with the sample) or build a rule straight on the raw payload.
import { Badge, Button, Card, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { UnmappedGroup } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";
import { JsonBlock } from "../components/JsonTree";
import { relativeTime } from "../components/time";

export function Unmapped() {
  const navigate = useNavigate();
  const groups = useQuery({ queryKey: ["unmapped"], queryFn: () => api<UnmappedGroup[]>("/unmapped"), refetchInterval: 10_000 });

  return (
    <Stack>
      <Title order={2}>Unmapped events</Title>
      <Text size="sm" c="dimmed">
        Events whose shape no mapping recognises. They still reach rules (as <code>$schema = unmapped</code>), but without $-fields. Map a shape once and every event like it gets
        $service, $severity, $summary and the rest — no code needed.
      </Text>
      <ErrorAlert error={groups.error} />
      {groups.isLoading && <Loader />}
      {groups.data?.length === 0 && <Text c="dimmed">Nothing unmapped. Every event that arrived matched a mapping.</Text>}
      {groups.data?.map((g) => (
        <Card key={`${g.sourceId}:${g.shapeHash}`} withBorder>
          <Stack gap="xs">
            <Group justify="space-between" wrap="wrap">
              <Group gap="xs">
                <Badge variant="outline">source {g.sourceId}</Badge>
                <Badge color="gray" variant="light">
                  {g.count} event{g.count === 1 ? "" : "s"}
                </Badge>
                <Text size="sm" c="dimmed" title={g.lastSeen}>
                  last {relativeTime(g.lastSeen)}
                </Text>
              </Group>
              <Group gap="xs">
                <Button size="xs" onClick={() => navigate("/mappings/new", { state: { sample: g.sample, sourceId: g.sourceId } })}>
                  Map this shape
                </Button>
                <Button size="xs" variant="light" onClick={() => navigate("/rules/new", { state: { sample: g.sample, sourceId: g.sourceId } })}>
                  Create rule from this event
                </Button>
              </Group>
            </Group>
            <Text size="xs" c="dimmed" style={{ overflowWrap: "anywhere" }}>
              keys: {g.keys.join(", ") || "(text line)"}
            </Text>
            <details>
              <summary>
                <Text span size="sm" c="dimmed">
                  Latest sample
                </Text>
              </summary>
              <JsonBlock value={g.sample} />
            </details>
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}
