// Route form (spec §4 Routing, contracts/route.schema.json). Recipient: a person, a team (optionally one role),
// or the team's on-call person. Channels: a fixed list, a schedule in the recipient's time zone with `otherwise`,
// or the recipient's own preferences. An empty route = the alert is only in the owner's UI feed.
import { ActionIcon, Button, Card, Checkbox, Group, MultiSelect, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";

import { CHANNELS, type Channel, type RouteEntry } from "../api/types";
import { BETWEEN, WEEKDAYS } from "../components/format";

type Recipient = RouteEntry["to"];
type Channels = RouteEntry["channels"];
interface Window {
  between: string;
  days?: number[];
  use: Channel[];
}
interface Schedule {
  schedule: Window[];
  otherwise: Channel[];
}

interface Props {
  value: RouteEntry[];
  onChange: (value: RouteEntry[]) => void;
  users: { id: string; name: string }[];
  teams: { id: string; name: string }[];
  readOnly?: boolean;
}

export function recipientKind(to: Recipient): "user" | "team" | "oncall" {
  if ("user" in to) return "user";
  if ("oncall" in to) return "oncall";
  return "team";
}

export function channelsMode(channels: Channels | undefined): "list" | "schedule" | "preferences" {
  if (Array.isArray(channels)) return "list";
  if (channels && typeof channels === "object") return "schedule";
  return "preferences";
}

/** Problems the UI blocks saving on (the API would answer 422 for most of them). */
export function routeProblems(route: RouteEntry[]): string[] {
  const out: string[] = [];
  route.forEach((entry, i) => {
    const n = `Recipient ${i + 1}`;
    const to = entry.to as Record<string, string>;
    if (!Object.values(to)[0]) out.push(`${n}: choose who`);
    const ch = entry.channels as Channels | undefined;
    if (Array.isArray(ch) && ch.length === 0) out.push(`${n}: choose at least one channel`);
    if (ch && typeof ch === "object" && !Array.isArray(ch)) {
      const s = ch as unknown as Schedule;
      s.schedule.forEach((w, j) => {
        if (!BETWEEN.test(w.between)) out.push(`${n}, window ${j + 1}: time must look like 08:00-20:00`);
        if (w.use.length === 0) out.push(`${n}, window ${j + 1}: choose at least one channel`);
      });
      if (s.schedule.length === 0) out.push(`${n}: add a time window or pick another channel mode`);
    }
  });
  return out;
}

export function RouteEditor({ value, onChange, users, teams, readOnly }: Props) {
  const set = (i: number, entry: RouteEntry) => onChange(value.map((e, j) => (j === i ? entry : e)));
  const userData = users.map((u) => ({ value: u.id, label: `${u.name} (${u.id})` }));
  const teamData = teams.map((t) => ({ value: t.id, label: t.name }));

  return (
    <Stack gap="xs">
      {value.length === 0 && (
        <Text size="sm" c="dimmed">
          Nobody is notified: the alert only appears in the owner’s alert feed (UI only).
        </Text>
      )}
      {value.map((entry, i) => {
        const kind = recipientKind(entry.to);
        const to = entry.to as Record<string, string>;
        const mode = channelsMode(entry.channels as Channels | undefined);
        return (
          <Card key={i} withBorder padding="xs">
            <Stack gap={6}>
              <Group gap="xs" align="end" wrap="wrap">
                <Select
                  size="xs"
                  label="Notify"
                  data={[
                    { value: "oncall", label: "On-call person of a team" },
                    { value: "team", label: "Whole team (or one role)" },
                    { value: "user", label: "One person" },
                  ]}
                  value={kind}
                  allowDeselect={false}
                  readOnly={readOnly}
                  onChange={(k) => {
                    if (!k || k === kind) return;
                    const first = k === "user" ? (users[0]?.id ?? "") : (teams[0]?.id ?? "");
                    set(i, { ...entry, to: { [k]: first } as Recipient });
                  }}
                  w={220}
                />
                {kind === "user" ? (
                  <Select size="xs" label="Person" data={userData} value={to.user || null} searchable allowDeselect={false} readOnly={readOnly} onChange={(u) => u && set(i, { ...entry, to: { user: u } })} w={240} />
                ) : (
                  <Select
                    size="xs"
                    label="Team"
                    data={teamData}
                    value={(kind === "oncall" ? to.oncall : to.team) || null}
                    searchable
                    allowDeselect={false}
                    readOnly={readOnly}
                    onChange={(t) => t && set(i, { ...entry, to: (kind === "oncall" ? { oncall: t } : { ...(to.role ? { role: to.role } : {}), team: t }) as Recipient })}
                    w={200}
                  />
                )}
                {kind === "team" && (
                  <Select
                    size="xs"
                    label="Role"
                    data={[
                      { value: "", label: "everyone" },
                      { value: "manager", label: "managers" },
                      { value: "member", label: "members" },
                    ]}
                    value={to.role ?? ""}
                    allowDeselect={false}
                    readOnly={readOnly}
                    onChange={(r) => set(i, { ...entry, to: (r ? { team: to.team, role: r } : { team: to.team }) as Recipient })}
                    w={130}
                  />
                )}
                {!readOnly && (
                  <ActionIcon variant="subtle" color="red" aria-label="Remove recipient" onClick={() => onChange(value.filter((_, j) => j !== i))} mb={4}>
                    ✕
                  </ActionIcon>
                )}
              </Group>
              <Group gap="xs" align="center">
                <Text size="xs" c="dimmed">
                  Channels:
                </Text>
                <SegmentedControl
                  size="xs"
                  disabled={readOnly}
                  value={mode}
                  onChange={(m) => {
                    if (m === mode) return;
                    const channels: Channels =
                      m === "list" ? ["email"] : m === "schedule" ? ({ schedule: [{ between: "08:00-20:00", use: ["sms"] }], otherwise: ["email"] } as Channels) : "preferences";
                    set(i, { ...entry, channels });
                  }}
                  data={[
                    { value: "list", label: "Always these" },
                    { value: "schedule", label: "By time of day" },
                    { value: "preferences", label: "Their preferences" },
                  ]}
                />
              </Group>
              {mode === "list" && (
                <ChannelPicker value={entry.channels as Channel[]} onChange={(c) => set(i, { ...entry, channels: c })} readOnly={readOnly} />
              )}
              {mode === "schedule" && (
                <ScheduleEditor value={entry.channels as unknown as Schedule} onChange={(s) => set(i, { ...entry, channels: s as unknown as Channels })} readOnly={readOnly} />
              )}
              {mode === "preferences" && (
                <Text size="xs" c="dimmed">
                  Each person gets the channels from their own preferences for the alert’s severity.
                </Text>
              )}
            </Stack>
          </Card>
        );
      })}
      {!readOnly && (
        <Group>
          <Button size="xs" variant="light" onClick={() => onChange([...value, { to: { oncall: teams[0]?.id ?? "" }, channels: ["sms"] } as RouteEntry])}>
            + Add recipient
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function ChannelPicker({ value, onChange, readOnly, label }: { value: Channel[]; onChange: (c: Channel[]) => void; readOnly?: boolean; label?: string }) {
  return (
    <Checkbox.Group value={value} onChange={(v) => onChange(CHANNELS.filter((c) => v.includes(c)))} label={label} size="xs">
      <Group gap="md" mt={label ? 4 : 0}>
        {CHANNELS.map((c) => (
          <Checkbox key={c} value={c} label={c} disabled={readOnly} />
        ))}
      </Group>
    </Checkbox.Group>
  );
}

function ScheduleEditor({ value, onChange, readOnly }: { value: Schedule; onChange: (s: Schedule) => void; readOnly?: boolean }) {
  const setWindow = (j: number, w: Window) => onChange({ ...value, schedule: value.schedule.map((x, k) => (k === j ? w : x)) });
  return (
    <Stack gap={6} pl="sm">
      <Text size="xs" c="dimmed">
        In the recipient’s time zone; the first matching window wins.
      </Text>
      {value.schedule.map((w, j) => (
        <Group key={j} gap="xs" align="end" wrap="wrap">
          <TextInput
            size="xs"
            label="Between"
            placeholder="08:00-20:00"
            value={w.between}
            error={BETWEEN.test(w.between) ? undefined : "HH:MM-HH:MM"}
            readOnly={readOnly}
            onChange={(e) => setWindow(j, { ...w, between: e.currentTarget.value })}
            w={130}
          />
          <MultiSelect
            size="xs"
            label="Days (empty = every day)"
            data={WEEKDAYS}
            value={(w.days ?? []).map(String)}
            readOnly={readOnly}
            onChange={(d) => {
              const days = d.map(Number).sort();
              const { days: _drop, ...rest } = w;
              void _drop;
              setWindow(j, days.length ? { ...rest, days } : rest);
            }}
            w={240}
          />
          <ChannelPicker value={w.use} onChange={(use) => setWindow(j, { ...w, use })} readOnly={readOnly} label="Use" />
          {!readOnly && value.schedule.length > 1 && (
            <ActionIcon variant="subtle" color="red" aria-label="Remove window" onClick={() => onChange({ ...value, schedule: value.schedule.filter((_, k) => k !== j) })}>
              ✕
            </ActionIcon>
          )}
        </Group>
      ))}
      {!readOnly && (
        <Button size="compact-xs" variant="subtle" w="fit-content" onClick={() => onChange({ ...value, schedule: [...value.schedule, { between: "20:00-08:00", use: ["phone"] }] })}>
          + Time window
        </Button>
      )}
      <ChannelPicker value={value.otherwise} onChange={(otherwise) => onChange({ ...value, otherwise })} readOnly={readOnly} label="Otherwise (outside all windows; none = no message)" />
    </Stack>
  );
}
