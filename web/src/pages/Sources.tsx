// Sources (admin): everything that sends events, and whether the system can read each source's events yet.
// Events it cannot read are shown on their source with one button to teach the format (the ConnectSource wizard).
import { Alert, Anchor, Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { MappingDoc, Source, UnmappedGroup } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";
import { PageHeader } from "../components/PageHeader";
import { relativeTime } from "../components/time";

export const SOURCE_KIND: Record<Source["type"], string> = {
  http: "App sends events over HTTP",
  kafka: "Kafka topic",
  logfile: "Log file",
};

export function Sources() {
  const navigate = useNavigate();
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/sources") });
  const mappings = useQuery({ queryKey: ["mappings"], queryFn: () => api<MappingDoc[]>("/mappings") });
  const unmapped = useQuery({ queryKey: ["unmapped"], queryFn: () => api<UnmappedGroup[]>("/unmapped"), refetchInterval: 10_000 });

  return (
    <Stack gap={0}>
      <PageHeader
        title="Sources"
        description="A source is anything that sends events: an app calling our HTTP endpoint, a Kafka topic or a log file. For each source the system needs to know which field is the service, how serious the event is and what the message says. Events it cannot read yet are listed on their source, so you can teach it the format in a few clicks."
        actions={<Button onClick={() => navigate("/sources/new")}>Connect a source</Button>}
      />
      <ErrorAlert error={sources.error ?? mappings.error ?? unmapped.error} />
      {sources.isLoading && <Loader />}
      {sources.data?.length === 0 && (
        <Alert color="gray" title="No sources yet">
          Connect your first source to start receiving events.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {sources.data?.map((s) => {
          const format = mappings.data?.find((m) => m.sources?.includes(s.id));
          const unread = (unmapped.data ?? []).filter((g) => g.sourceId === s.id);
          const unreadCount = unread.reduce((n, g) => n + g.count, 0);
          const latest = unread[0];
          return (
            <Card key={s.id} withBorder padding="lg">
              <Stack gap="sm">
                <Group justify="space-between" wrap="nowrap">
                  <Text fw={600} size="lg">
                    {s.id}
                  </Text>
                  {!s.enabled && <Badge color="gray">Disabled</Badge>}
                </Group>
                <Text size="sm" c="dimmed">
                  {SOURCE_KIND[s.type]}
                  {s.type === "kafka" && s.config?.topic ? `: ${String(s.config.topic)}` : ""}
                  {s.type === "logfile" && s.config?.path ? `: ${String(s.config.path)}` : ""}
                </Text>
                {unreadCount > 0 ? (
                  <Alert color="orange" variant="light" p="sm">
                    <Group justify="space-between" wrap="wrap" gap="xs">
                      <Text size="sm">
                        <b>{unreadCount}</b> event{unreadCount === 1 ? "" : "s"} the system can't read yet (last {relativeTime(latest.lastSeen)})
                      </Text>
                      <Button size="xs" color="orange" onClick={() => navigate(`/sources/${s.id}/connect`, { state: { sample: latest.sample } })}>
                        Teach the format
                      </Button>
                    </Group>
                  </Alert>
                ) : format ? (
                  <Text size="sm" c="green">
                    ✓ Events are understood
                  </Text>
                ) : (
                  <Text size="sm" c="dimmed">
                    No unreadable events. Shared formats cover this source, or nothing has arrived yet.
                  </Text>
                )}
                <Group gap="xs">
                  <Button size="xs" variant="light" onClick={() => navigate(`/sources/${s.id}/connect`)}>
                    {format ? "Change format" : "Set up format"}
                  </Button>
                  <Button size="xs" variant="subtle" onClick={() => navigate("/rules/new", { state: { sample: latest?.sample, sourceId: s.id } })}>
                    New alert rule
                  </Button>
                </Group>
              </Stack>
            </Card>
          );
        })}
      </SimpleGrid>
      <Text size="sm" c="dimmed" mt="xl">
        Advanced: <Anchor component={Link} to="/mappings">shared formats</Anchor> apply to every source, and the{" "}
        <Anchor component={Link} to="/unmapped">raw list of unreadable events</Anchor>.
      </Text>
    </Stack>
  );
}
