// My notification preferences (spec §6, §9 P2): channels per severity, working hours and time zone. Used when a
// rule routes with "their preferences"; a channel list in the rule wins over these.
import { Alert, Badge, Button, Card, Checkbox, Group, Loader, MultiSelect, Select, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { CHANNELS, SEVERITIES, type Channel, type Preferences as Prefs, type Severity } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";
import { BETWEEN, TIMEZONES, WEEKDAYS } from "../components/format";
import { WorkTimeSection } from "../work/work";

export function Preferences() {
  const prefs = useQuery({ queryKey: ["preferences"], queryFn: () => api<Prefs>("/preferences/me") });
  if (prefs.isLoading) return <Loader />;
  if (!prefs.data) return <ErrorAlert error={prefs.error} />;
  return <Form initial={prefs.data} />;
}

export function preferencesProblems(p: Prefs): string[] {
  const out: string[] = [];
  if (p.workingHours) {
    if (!BETWEEN.test(p.workingHours.between ?? "")) out.push("Working hours must look like 08:00-18:00");
    if (!Object.keys(p.bySeverity ?? {}).length) out.push("Working hours apply to the severities you set yourself: choose channels for at least one severity");
  }
  return out;
}

function Form({ initial }: { initial: Prefs }) {
  const client = useQueryClient();
  const [p, setP] = useState<Prefs>(initial);
  const save = useMutation({
    mutationFn: () => api<Prefs>("/preferences/me", { method: "PUT", json: p }),
    onSuccess: (saved) => {
      client.setQueryData(["preferences"], saved);
      setP(saved);
      void client.invalidateQueries({ queryKey: ["me"] });
    },
  });
  const by = p.bySeverity ?? {};
  const setLevel = (level: Severity, channels: Channel[] | undefined) => {
    const next = { ...by };
    if (channels === undefined) delete next[level];
    else next[level] = channels;
    setP({ ...p, bySeverity: next });
  };
  const problems = preferencesProblems(p);
  const hours = p.workingHours;

  return (
    <Stack maw={760}>
      <Title order={2}>My notifications</Title>
      <Text size="sm" c="dimmed">
        Used when a rule sends to “their preferences”. If a rule names channels itself, the rule wins. The UI feed always shows your alerts.
      </Text>

      <Card withBorder>
        <Title order={4} mb="xs">
          Work time
        </Title>
        <WorkTimeSection />
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">
          Channels per severity
        </Title>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Use team default</Table.Th>
              {CHANNELS.map((c) => (
                <Table.Th key={c}>{c}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {[...SEVERITIES].reverse().map((level) => {
              const own = by[level];
              return (
                <Table.Tr key={level}>
                  <Table.Td>
                    <Badge variant="light">{level}</Badge>
                  </Table.Td>
                  <Table.Td>
                    <Switch aria-label={`${level}: team default`} checked={own === undefined} onChange={(e) => setLevel(level, e.currentTarget.checked ? undefined : ["ui"])} />
                  </Table.Td>
                  {CHANNELS.map((c) => (
                    <Table.Td key={c}>
                      <Checkbox
                        aria-label={`${level} by ${c}`}
                        disabled={own === undefined}
                        checked={Boolean(own?.includes(c))}
                        onChange={(e) => setLevel(level, e.currentTarget.checked ? CHANNELS.filter((x) => x === c || own?.includes(x)) : (own ?? []).filter((x) => x !== c))}
                      />
                    </Table.Td>
                  ))}
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">
          Working hours
        </Title>
        <Stack gap="xs">
          <Select label="My time zone" data={TIMEZONES} searchable value={p.timezone ?? "UTC"} onChange={(tz) => tz && setP({ ...p, timezone: tz })} w={300} />
          <Switch
            label="Use other channels outside working hours"
            checked={Boolean(hours)}
            onChange={(e) => setP(e.currentTarget.checked ? { ...p, workingHours: { between: "08:00-18:00", days: [1, 2, 3, 4, 5] }, outsideWorkingHours: p.outsideWorkingHours ?? ["email"] } : { ...p, workingHours: null, outsideWorkingHours: undefined })}
          />
          {hours && (
            <>
              <Group align="start">
                <TextInput
                  label="Working hours"
                  placeholder="08:00-18:00"
                  value={hours.between ?? ""}
                  error={BETWEEN.test(hours.between ?? "") ? undefined : "HH:MM-HH:MM"}
                  onChange={(e) => setP({ ...p, workingHours: { ...hours, between: e.currentTarget.value } })}
                  w={160}
                />
                <MultiSelect
                  label="Days (empty = every day)"
                  data={WEEKDAYS}
                  value={(hours.days ?? []).map(String)}
                  onChange={(d) => setP({ ...p, workingHours: { ...hours, days: d.map(Number).sort() } })}
                  w={320}
                />
              </Group>
              <Checkbox.Group
                label="Outside working hours use"
                description="None ticked = no messages outside working hours (the UI feed still shows everything)"
                value={p.outsideWorkingHours ?? []}
                onChange={(v) => setP({ ...p, outsideWorkingHours: CHANNELS.filter((c) => v.includes(c)) })}
              >
                <Group mt={4}>
                  {CHANNELS.map((c) => (
                    <Checkbox key={c} value={c} label={c} />
                  ))}
                </Group>
              </Checkbox.Group>
            </>
          )}
        </Stack>
      </Card>

      {problems.length > 0 && <Alert color="yellow">{problems.join(" · ")}</Alert>}
      <ErrorAlert error={save.error} title="Not saved" />
      <Group>
        <Button onClick={() => save.mutate()} loading={save.isPending} disabled={problems.length > 0}>
          Save
        </Button>
        {save.isSuccess && <Badge color="green">Saved</Badge>}
      </Group>
    </Stack>
  );
}
