// On-call (spec §6 "Dyżury", §9 P3): every team's rotation and who is on duty now; a team's managers edit it.
import { ActionIcon, Alert, Badge, Button, Card, Group, Loader, Select, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { useTeams, useUsers } from "../api/directory";
import type { OncallSchedule, Team } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { DURATION, HHMM, TIMEZONES } from "../components/format";
import { formatTime } from "../components/time";

export function Oncall() {
  const perms = usePermissions();
  const teams = useTeams();
  const users = useUsers();
  const schedules = useQuery({ queryKey: ["oncall"], queryFn: () => api<OncallSchedule[]>("/oncall"), refetchInterval: 60_000 });
  const name = (id: string | null | undefined) => (id ? (users.data?.find((u) => u.id === id)?.name ?? id) : "nobody");

  if (teams.isLoading || schedules.isLoading) return <Loader />;
  const byTeam = new Map((schedules.data ?? []).map((s) => [s.teamId, s]));
  const myTeams = new Set(perms.me?.teams.map((t) => t.id));
  const ordered = [...(teams.data ?? [])].sort((a, b) => Number(myTeams.has(b.id)) - Number(myTeams.has(a.id)) || a.name.localeCompare(b.name));

  return (
    <Stack>
      <Title order={2}>On-call</Title>
      <Text size="sm" c="dimmed">
        Rules that notify “on-call person of a team” reach whoever is on duty at that moment.
      </Text>
      <ErrorAlert error={teams.error ?? schedules.error} />
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {ordered.map((team) => (
          <TeamRotation key={team.id} team={team} schedule={byTeam.get(team.id)} canEdit={perms.isManager(team.id)} name={name} />
        ))}
      </SimpleGrid>
    </Stack>
  );
}

function TeamRotation({ team, schedule, canEdit, name }: { team: Team; schedule?: OncallSchedule; canEdit: boolean; name: (id?: string | null) => string }) {
  const [editing, setEditing] = useState(false);
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Text fw={600}>{team.name}</Text>
        {canEdit && !editing && (
          <Button size="xs" variant="light" onClick={() => setEditing(true)}>
            {schedule ? "Edit rotation" : "Set up rotation"}
          </Button>
        )}
      </Group>
      {editing ? (
        <RotationForm team={team} schedule={schedule} name={name} onDone={() => setEditing(false)} />
      ) : schedule ? (
        <Stack gap={4}>
          <Group gap="xs">
            <Text size="sm">On duty now:</Text>
            <Badge color="green" size="lg">
              {name(schedule.currentUserId)}
            </Badge>
            <Text size="xs" c="dimmed">
              until {formatTime(schedule.currentUntil)}
            </Text>
          </Group>
          <Text size="sm" c="dimmed">
            Shifts of {schedule.shiftLength}, handover at {schedule.handoffTime} ({schedule.timezone}), from {schedule.startsOn}
          </Text>
          <Text size="sm">Order: {schedule.rotation.map((u) => name(u)).join(" → ")}</Text>
        </Stack>
      ) : (
        <Text size="sm" c="dimmed">
          No rotation yet{canEdit ? "" : " (a manager of the team sets it up)"}.
        </Text>
      )}
    </Card>
  );
}

function RotationForm({ team, schedule, name, onDone }: { team: Team; schedule?: OncallSchedule; name: (id?: string | null) => string; onDone: () => void }) {
  const client = useQueryClient();
  const [rotation, setRotation] = useState<string[]>(schedule?.rotation ?? team.members.map((m) => m.userId));
  const [shiftLength, setShiftLength] = useState(schedule?.shiftLength ?? "7d");
  const [handoffTime, setHandoffTime] = useState(schedule?.handoffTime ?? "09:00");
  const [timezone, setTimezone] = useState(schedule?.timezone ?? "UTC");
  const [startsOn, setStartsOn] = useState(schedule?.startsOn ?? new Date().toISOString().slice(0, 10));

  const save = useMutation({
    mutationFn: () => api<OncallSchedule>(`/oncall/${team.id}`, { method: "PUT", json: { teamId: team.id, rotation, shiftLength, handoffTime, timezone, startsOn } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["oncall"] });
      onDone();
    },
  });
  const notIn = team.members.map((m) => m.userId).filter((u) => !rotation.includes(u));
  const move = (i: number, d: -1 | 1) => {
    const next = [...rotation];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setRotation(next);
  };
  const problems = [
    rotation.length === 0 && "Add at least one person",
    !DURATION.test(shiftLength) && "Shift length like 1d, 12h or 7d",
    !HHMM.test(handoffTime) && "Handover time like 09:00",
    !startsOn && "Choose the first day",
  ].filter(Boolean) as string[];

  return (
    <Stack gap="xs">
      <Text size="sm" fw={500}>
        Order (first person takes the first shift)
      </Text>
      {rotation.map((u, i) => (
        <Group key={u} gap="xs">
          <Badge variant="light">{i + 1}</Badge>
          <Text size="sm" style={{ flex: 1 }}>
            {name(u)}
          </Text>
          <ActionIcon variant="subtle" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
            ↑
          </ActionIcon>
          <ActionIcon variant="subtle" aria-label="Move down" disabled={i === rotation.length - 1} onClick={() => move(i, 1)}>
            ↓
          </ActionIcon>
          <ActionIcon variant="subtle" color="red" aria-label="Remove" onClick={() => setRotation(rotation.filter((x) => x !== u))}>
            ✕
          </ActionIcon>
        </Group>
      ))}
      {notIn.length > 0 && (
        <Select size="xs" placeholder="+ Add a team member" data={notIn.map((u) => ({ value: u, label: name(u) }))} value={null} onChange={(u) => u && setRotation([...rotation, u])} w={240} />
      )}
      <Group grow align="start">
        <TextInput label="Shift length" placeholder="7d" value={shiftLength} onChange={(e) => setShiftLength(e.currentTarget.value)} />
        <TextInput label="Handover at" placeholder="09:00" value={handoffTime} onChange={(e) => setHandoffTime(e.currentTarget.value)} />
      </Group>
      <Group grow align="start">
        <Select label="Time zone" data={TIMEZONES} searchable value={timezone} allowDeselect={false} onChange={(t) => t && setTimezone(t)} />
        <TextInput label="First shift starts on" type="date" value={startsOn} onChange={(e) => setStartsOn(e.currentTarget.value)} />
      </Group>
      {problems.length > 0 && <Alert color="yellow" p="xs">{problems.join(" · ")}</Alert>}
      <ErrorAlert error={save.error} title="Not saved" />
      <Group>
        <Button size="xs" onClick={() => save.mutate()} loading={save.isPending} disabled={problems.length > 0}>
          Save rotation
        </Button>
        <Button size="xs" variant="default" onClick={onDone}>
          Cancel
        </Button>
      </Group>
    </Stack>
  );
}
