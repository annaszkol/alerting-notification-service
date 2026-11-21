// Rule editor (spec §4, §9 P1): condition form AND / OR / NOT, routing, presentation, grouping, auto-resolve and
// runbooks, with the tester next to it. Saves one JSON document; the processor picks it up from the next event
// (config_version + NOTIFY config_changed), no restart (§13 no. 6).
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  List,
  Loader,
  MultiSelect,
  Select,
  Stack,
  Switch,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { describeCondition } from "../rules/condition";
import { useRunbooks, useTeams, useUsers } from "../api/directory";
import { SEVERITIES, type AlertDetail, type Owner, type RuleDoc, type Severity } from "../api/types";
import { ownerLabel, usePermissions } from "../auth/permissions";
import { ConfirmButton } from "../components/ConfirmButton";
import { ErrorAlert } from "../components/ErrorAlert";
import { slugify } from "../components/format";
import { JsonBlock } from "../components/JsonTree";
import { leafPaths, LETTER_FIELDS, LIVE_FIELDS } from "../components/paths";
import { formatTime } from "../components/time";
import { ConditionBuilder } from "../rules/ConditionBuilder";
import { RouteEditor } from "../rules/RouteEditor";
import { EMPTY_SAMPLE, parseSample, RuleTester, type Sample } from "../rules/RuleTester";
import { newRule, routeSummary, ruleProblems, toInput } from "../rules/ruleDoc";

const COLORS = ["red", "orange", "yellow", "green", "blue", "grape", "gray"];

export interface RuleEditorState {
  /** raw payload to put into the tester, e.g. from "Create rule from this event" */
  sample?: unknown;
  sourceId?: string;
}

const ownerKey = (o: Owner) => ("team" in o ? `team:${o.team}` : `user:${o.user}`);
const ownerFromKey = (k: string): Owner => (k.startsWith("team:") ? { team: k.slice(5) } : { user: k.slice(5) });

export function RuleEditor() {
  const { id } = useParams();
  const isNew = !id;
  const existing = useQuery({ queryKey: ["rule", id], queryFn: () => api<RuleDoc>(`/rules/${id}`), enabled: !isNew });
  if (!isNew && existing.isLoading) return <Loader />;
  if (!isNew && (existing.error || !existing.data)) return <ErrorAlert error={existing.error ?? new Error("Rule not found")} />;
  return <RuleForm key={id ?? "new"} initial={existing.data} />;
}

function RuleForm({ initial }: { initial?: RuleDoc }) {
  const perms = usePermissions();
  const users = useUsers();
  const teams = useTeams();
  const runbooks = useRunbooks();
  const navigate = useNavigate();
  const client = useQueryClient();
  const location = useLocation();
  const [params] = useSearchParams();
  const isNew = !initial;

  const defaultOwner: Owner = useMemo(() => {
    const managed = perms.managedTeams(teams.data);
    return managed.length ? { team: managed[0] } : { user: perms.userId ?? "" };
  }, [perms, teams.data]);

  const [doc, setDoc] = useState<RuleDoc>(() => initial ?? newRule(defaultOwner));
  const [idTouched, setIdTouched] = useState(false);
  const [sample, setSample] = useState<Sample>(() => {
    const state = location.state as RuleEditorState | null;
    const alertId = Number(params.get("alert"));
    if (alertId) return { ...EMPTY_SAMPLE, mode: "alert", alertId };
    if (state?.sample !== undefined) {
      return { ...EMPTY_SAMPLE, text: typeof state.sample === "string" ? state.sample : JSON.stringify(state.sample, null, 2), sourceId: state.sourceId ?? "" };
    }
    return EMPTY_SAMPLE;
  });

  // a new rule waits for /me and /teams to pick a sensible owner
  const owner = doc.owner && Object.values(doc.owner)[0] ? doc.owner : defaultOwner;
  const current: RuleDoc = { ...doc, owner };
  const set = (patch: Partial<RuleDoc>) => setDoc((d) => ({ ...d, owner, ...patch }));

  const canEdit = isNew ? perms.canEditOwner(owner) : perms.canEditOwner(initial.owner);
  const readOnly = !canEdit;

  // field suggestions: letter fields + every path of the sample payload (or the alert's first event)
  const sampleAlert = useQuery({
    queryKey: ["alert", sample.alertId],
    queryFn: () => api<AlertDetail>(`/alerts/${sample.alertId}`),
    enabled: sample.mode === "alert" && Boolean(sample.alertId),
  });
  const samplePayload = sample.mode === "alert" ? sampleAlert.data?.firstEvent?.payload : sample.text.trim() ? parseSample(sample.text).payload : undefined;
  const paths = useMemo(() => {
    const fromSample = samplePayload !== undefined && typeof samplePayload === "object" ? leafPaths(samplePayload).map((p) => p.path) : [];
    return [...LETTER_FIELDS, ...fromSample];
  }, [samplePayload]);

  const ownerOptions = useMemo(() => {
    const opts = new Map<string, string>();
    for (const t of perms.managedTeams(teams.data)) opts.set(`team:${t}`, `Team ${teams.data?.find((x) => x.id === t)?.name ?? t}`);
    if (perms.userId) opts.set(`user:${perms.userId}`, "Personal (only me)");
    if (perms.isAdmin) for (const u of users.data ?? []) if (u.id !== perms.userId) opts.set(`user:${u.id}`, `Personal rule of ${u.name}`);
    opts.set(ownerKey(owner), opts.get(ownerKey(owner)) ?? ownerLabel(owner));
    return [...opts].map(([value, label]) => ({ value, label }));
  }, [perms, teams.data, users.data, owner]);

  const problems = ruleProblems(current);
  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api<RuleDoc>("/rules", { json: toInput(current) })
        : api<RuleDoc>(`/rules/${initial.id}`, { method: "PUT", json: toInput(current) }),
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ["rules"] });
      client.setQueryData(["rule", saved.id], saved);
      if (isNew) navigate(`/rules/${saved.id}`, { replace: true });
      else setDoc(saved);
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/rules/${initial?.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rules"] });
      navigate("/rules");
    },
  });

  const present = current.present ?? {};
  const dedup: NonNullable<RuleDoc["dedup"]> = current.dedup ?? { mode: "until_resolved" };
  const teamList = (teams.data ?? []).map((t) => ({ id: t.id, name: t.name }));
  const userList = (users.data ?? []).map((u) => ({ id: u.id, name: u.name }));

  return (
    <Stack>
      <Anchor component={Link} to="/rules" size="sm">
        ← All rules
      </Anchor>
      <Group justify="space-between" align="end">
        <Title order={2}>{isNew ? "New rule" : `Rule ${initial.id}`}</Title>
        {!isNew && (
          <Text size="sm" c="dimmed">
            last changed by {initial.updatedBy}, {formatTime(initial.updatedAt)}
          </Text>
        )}
      </Group>
      {readOnly && (
        <Alert color="gray">
          You can view and test this rule. Only {"team" in owner ? `a manager of team ${owner.team}` : `its owner (${owner.user})`} or an admin can change it.
        </Alert>
      )}

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack>
            <Card withBorder padding="md" bg="var(--mantine-color-default-hover)">
              <Text size="xs" tt="uppercase" fw={700} c="dimmed" mb={4}>
                This rule in one sentence
              </Text>
              <Text>
                <b>When</b> {describeCondition(current.when)}, <b>notify</b> {routeSummary(current.route)}.
              </Text>
            </Card>
            <Card withBorder>
              <Title order={4}>1. When</Title>
              <Text size="sm" c="dimmed" mb="sm">
                Which events should raise this alert? Pick a field, then what it should be. Paste an example event in the tester on the right to pick from its fields too.
              </Text>
              <ConditionBuilder value={current.when} onChange={(when) => set({ when })} paths={paths} readOnly={readOnly} />
            </Card>
            <Card withBorder>
              <Title order={4}>2. Notify</Title>
              <Text size="sm" c="dimmed" mb="sm">
                Who hears about it, and how. Without anyone here the alert only shows in the app.
              </Text>
              <RouteEditor value={current.route ?? []} onChange={(route) => set({ route })} users={userList} teams={teamList} readOnly={readOnly} />
            </Card>
            <Card withBorder>
              <Title order={4}>3. Name it</Title>
              <Text size="sm" c="dimmed" mb="sm">
                The alert title people see. Use {"{{field}}"} to put a value from the event in it.
              </Text>
              <Stack gap="xs">
                <Group grow align="start">
                  <TextInput
                    label="Alert title"
                    description="e.g. {{$service}} or {{workflowId}}; {{$count}} shows how many times it happened"
                    placeholder="Settlement failed: {{workflowId}}"
                    value={present.title ?? ""}
                    readOnly={readOnly}
                    onChange={(e) => {
                      const title = e.currentTarget.value;
                      set({ present: { ...present, title }, ...(isNew && !idTouched ? { id: slugify(title.replace(/\{\{[^}]*\}\}/g, "")) } : {}) });
                    }}
                  />
                </Group>
                <Group grow align="start">
                  <TextInput
                    label="Rule name"
                    description={isNew ? "Filled from the title; cannot be changed later" : "Cannot be changed"}
                    value={current.id}
                    readOnly={!isNew || readOnly}
                    onChange={(e) => {
                      setIdTouched(true);
                      set({ id: e.currentTarget.value });
                    }}
                  />
                  <Select
                    label="Owner"
                    description="Whose alert it is: it shows in their alert list"
                    data={ownerOptions}
                    value={ownerKey(owner)}
                    allowDeselect={false}
                    readOnly={readOnly}
                    onChange={(k) => k && set({ owner: ownerFromKey(k) })}
                  />
                </Group>
                <Switch label="Rule is on" checked={current.enabled !== false} disabled={readOnly} onChange={(e) => set({ enabled: e.currentTarget.checked })} />
              </Stack>
            </Card>
            <Accordion variant="contained" radius="md">
              <Accordion.Item value="grouping">
                <Accordion.Control>More options: grouping, auto-resolve, runbooks</Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="xs">
                <TagsInput
                  label="Group events by"
                  description="Same values = same alert. Empty = $correlationKey, else $service + $summary"
                  data={paths}
                  value={dedup.key ?? []}
                  readOnly={readOnly}
                  onChange={(key) => set({ dedup: { ...dedup, key } })}
                  clearable
                />
                <Group grow align="start">
                  <Select
                    label="One alert"
                    data={[
                      { value: "until_resolved", label: "until it is resolved" },
                      { value: "window", label: "per time window" },
                    ]}
                    value={dedup.mode ?? "until_resolved"}
                    allowDeselect={false}
                    readOnly={readOnly}
                    onChange={(mode) => set({ dedup: { ...dedup, mode: mode as "until_resolved" | "window" } })}
                  />
                  {dedup.mode === "window" && (
                    <TextInput label="Window" placeholder="5m" value={dedup.window ?? ""} readOnly={readOnly} onChange={(e) => set({ dedup: { ...dedup, window: e.currentTarget.value } })} />
                  )}
                  <TextInput
                    label="Auto-resolve after silence"
                    placeholder="e.g. 30m (empty = never)"
                    value={current.autoResolveAfter ?? ""}
                    readOnly={readOnly}
                    onChange={(e) => set({ autoResolveAfter: e.currentTarget.value || null })}
                  />
                </Group>
                <MultiSelect
                  label="Runbooks shown on the alert"
                  data={(runbooks.data ?? []).map((r) => ({ value: r.id, label: `${r.title} (${r.owner.team})` }))}
                  value={current.runbooks ?? []}
                  readOnly={readOnly}
                  searchable
                  onChange={(ids) => set({ runbooks: ids })}
                />
              </Stack>
                </Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="looks">
                <Accordion.Control>More options: how the alert looks</Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="xs">
                <Textarea
                  label="Body template"
                  placeholder="Step {{step}}: {{error.message}}"
                  autosize
                  minRows={2}
                  value={present.body ?? ""}
                  readOnly={readOnly}
                  onChange={(e) => set({ present: { ...present, body: e.currentTarget.value } })}
                />
                <TagsInput
                  label="Fields shown on the alert"
                  description="Paths; press Enter after each"
                  data={paths}
                  value={present.fields ?? []}
                  readOnly={readOnly}
                  onChange={(fields) => set({ present: { ...present, fields } })}
                  clearable
                />
                <Group grow>
                  <Select label="Colour" placeholder="default" data={COLORS} value={present.color ?? null} readOnly={readOnly} clearable onChange={(color) => set({ present: { ...present, color: color ?? undefined } })} />
                  <Select
                    label="Severity override"
                    description="e.g. business events without a severity"
                    placeholder="from the event"
                    data={SEVERITIES}
                    value={present.severity ?? null}
                    readOnly={readOnly}
                    clearable
                    onChange={(s) => set({ present: { ...present, severity: (s as Severity) ?? undefined } })}
                  />
                </Group>
                <Text size="xs" c="dimmed">
                  Live fields: {LIVE_FIELDS.join(", ")} are filled when the alert is shown or sent.
                </Text>
              </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <details>
              <summary>
                <Text span size="sm" c="dimmed">
                  Rule JSON (read only)
                </Text>
              </summary>
              <JsonBlock value={toInput(current)} />
            </details>

            {!readOnly && (
              <Card withBorder>
                <Stack gap="xs">
                  {problems.length > 0 && (
                    <Alert color="yellow" title="Before saving">
                      <List size="sm">
                        {problems.map((p) => (
                          <List.Item key={p}>{p}</List.Item>
                        ))}
                      </List>
                    </Alert>
                  )}
                  <ErrorAlert error={save.error ?? remove.error} title="Not saved" />
                  {save.isSuccess && !save.isPending && <Badge color="green">Saved — active from the next event</Badge>}
                  <Group justify="space-between">
                    <Button onClick={() => save.mutate()} loading={save.isPending} disabled={problems.length > 0}>
                      {isNew ? "Create rule" : "Save changes"}
                    </Button>
                    {!isNew && (
                      <ConfirmButton title={`Delete rule ${initial.id}?`} message="Open alerts of this rule stay as they are. This cannot be undone." onConfirm={() => remove.mutate()} loading={remove.isPending}>
                        Delete rule
                      </ConfirmButton>
                    )}
                  </Group>
                </Stack>
              </Card>
            )}
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Card withBorder style={{ position: "sticky", top: 72 }}>
            <Title order={4} mb="xs">
              Test
            </Title>
            <RuleTester rule={current} sample={sample} onSample={setSample} blocked={problems.find((p) => p.startsWith("Condition") || p.startsWith("Recipient")) ?? null} />
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
