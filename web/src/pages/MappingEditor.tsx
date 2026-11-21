// Mapping editor (spec §3 step 4, §9 P2, admin): click fields in the sample's JSON tree to assign them to $-fields,
// translate severity values, write the $summary template, set the detect condition, and watch the letter update
// live (POST /mappings/preview). No code.
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  MultiSelect,
  NumberInput,
  Radio,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { useSources } from "../api/directory";
import type { Condition, MappingDoc, PreviewResult } from "../api/types";
import { ConfirmButton } from "../components/ConfirmButton";
import { ErrorAlert } from "../components/ErrorAlert";
import { slugError } from "../components/format";
import { JsonBlock, JsonTree } from "../components/JsonTree";
import { leafPaths, showValue } from "../components/paths";
import {
  applyPick,
  MAPPED_FIELDS,
  modesFor,
  parseField,
  serializeField,
  SEVERITY_VALUES,
  STATUS_VALUES,
  TIME_FORMATS,
  type FieldSpec,
} from "../mappings/mappingFields";
import { conditionProblems, parseCondition } from "../rules/condition";
import { ConditionBuilder } from "../rules/ConditionBuilder";
import { parseSample } from "../rules/RuleTester";
import { useMappings } from "./Mappings";

const STANDARD = new Set<string>(MAPPED_FIELDS.map((f) => f.name));

export function MappingEditor() {
  const { id } = useParams();
  const mappings = useMappings();
  if (id && mappings.isLoading) return <Loader />;
  const existing = id ? mappings.data?.find((m) => m.id === id) : undefined;
  if (id && !existing) return <ErrorAlert error={mappings.error ?? new Error(`Mapping ${id} not found`)} />;
  return <MappingForm key={id ?? "new"} initial={existing} />;
}

function initialFromSample(sample: unknown, sourceId: string | undefined): MappingDoc {
  const top = sample && typeof sample === "object" && !Array.isArray(sample) ? Object.keys(sample).slice(0, 3) : [];
  return {
    id: "",
    priority: 100,
    sources: sourceId ? [sourceId] : [],
    ...(top.length ? { detect: { all: top.map((k) => ({ [k]: { exists: true } })) } } : {}),
    fields: {},
  };
}

function MappingForm({ initial }: { initial?: MappingDoc }) {
  const isNew = !initial;
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const sources = useSources(true);
  const state = location.state as { sample?: unknown; sourceId?: string } | null;

  const [doc, setDoc] = useState<MappingDoc>(() => initial ?? initialFromSample(state?.sample, state?.sourceId));
  const [specs, setSpecs] = useState<Record<string, FieldSpec>>(() =>
    Object.fromEntries(MAPPED_FIELDS.map((f) => [f.name, parseField(doc.fields[f.name] ?? "")])),
  );
  const [target, setTarget] = useState<string>("$service");
  const [sampleText, setSampleText] = useState(() =>
    state?.sample === undefined ? "" : typeof state.sample === "string" ? state.sample : JSON.stringify(state.sample, null, 2),
  );
  const [sampleSource, setSampleSource] = useState(state?.sourceId ?? initial?.sources?.[0] ?? "");

  // fields = the standard rows + any non-standard keys the mapping already had (kept untouched)
  const fields = useMemo(() => {
    const out: Record<string, unknown> = Object.fromEntries(Object.entries(doc.fields).filter(([k]) => !STANDARD.has(k)));
    for (const f of MAPPED_FIELDS) {
      const v = serializeField(specs[f.name]);
      if (v !== undefined) out[f.name] = v;
    }
    return out;
  }, [doc.fields, specs]);
  const mapping: MappingDoc = { ...doc, id: doc.id.trim(), fields };
  // the contract allows no null for these two: leave them out instead
  if (mapping.detect == null) delete mapping.detect;
  if (mapping.sources == null) delete mapping.sources;

  const sample = sampleText.trim() ? parseSample(sampleText) : null;
  const [debounced] = useDebouncedValue({ mapping, sampleText, sampleSource }, 400);
  const preview = useQuery({
    queryKey: ["mapping-preview", debounced],
    enabled: Boolean(debounced.sampleText.trim()),
    queryFn: () => {
      const s = parseSample(debounced.sampleText);
      return api<PreviewResult>("/mappings/preview", {
        json: {
          mapping: { ...debounced.mapping, id: debounced.mapping.id || "preview" },
          sample: { payload: s.payload, ...(debounced.sampleSource ? { sourceId: debounced.sampleSource } : {}), contentType: s.json ? "application/json" : "text/plain" },
        },
      });
    },
    placeholderData: (prev) => prev,
  });
  const letter = preview.data?.letters[0];
  // the tree shows the parsed element (after the source parser and splitOn), so clicked paths are what fields read
  const treeValue = letter?.payload ?? sample?.payload;

  const detectPaths = useMemo(() => ["$sourceId", "$sourceType", ...(sample && typeof sample.payload === "object" ? leafPaths(sample.payload).map((p) => p.path) : [])], [sample]);
  const marked = useMemo(() => new Set(Object.values(specs).map((s) => s.path).filter(Boolean)), [specs]);

  const setSpec = (name: string, spec: FieldSpec) => setSpecs((s) => ({ ...s, [name]: spec }));
  const pick = (path: string) => setSpec(target, applyPick(specs[target], path));

  const problems: string[] = [];
  const idProblem = slugError(mapping.id);
  if (idProblem) problems.push(`Mapping id: ${idProblem}`);
  if (doc.detect) for (const p of conditionProblems(parseCondition(doc.detect))) problems.push(`Detect: ${p.message}`);
  if (Object.keys(fields).length === 0) problems.push("Map at least one field");

  const save = useMutation({
    mutationFn: () => (isNew ? api<MappingDoc>("/mappings", { json: mapping }) : api<MappingDoc>(`/mappings/${initial.id}`, { method: "PUT", json: mapping })),
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ["mappings"] });
      void client.invalidateQueries({ queryKey: ["unmapped"] });
      if (isNew) navigate(`/mappings/${saved.id}`, { replace: true, state: { sample: sample?.payload, sourceId: sampleSource } });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/mappings/${initial?.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["mappings"] });
      navigate("/mappings");
    },
  });

  const catchesAll = !doc.detect && !doc.sources?.length;

  return (
    <Stack>
      <Anchor component={Link} to="/mappings" size="sm">
        ← All mappings
      </Anchor>
      <Title order={2}>{isNew ? "New mapping" : `Mapping ${initial.id}`}</Title>
      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack>
            <Card withBorder>
              <Title order={4} mb="xs">
                1. Which events
              </Title>
              <Stack gap="xs">
                <Group grow align="start">
                  <TextInput label="Mapping id" description="Also the $schema when not set below" value={doc.id} readOnly={!isNew} onChange={(e) => setDoc({ ...doc, id: e.currentTarget.value })} />
                  <NumberInput label="Priority" description="Lower is tried first" value={doc.priority ?? 100} onChange={(v) => setDoc({ ...doc, priority: typeof v === "number" ? v : 100 })} />
                </Group>
                <MultiSelect
                  label="Only from these sources"
                  description="Empty = every source"
                  data={(sources.data ?? []).map((s) => s.id).concat((doc.sources ?? []).filter((s) => !sources.data?.some((x) => x.id === s)))}
                  value={doc.sources ?? []}
                  onChange={(s) => setDoc({ ...doc, sources: s })}
                  searchable
                />
                <Switch
                  label="Only events matching a condition (detect)"
                  checked={Boolean(doc.detect)}
                  onChange={(e) => {
                    const { detect: _d, ...rest } = doc;
                    void _d;
                    setDoc(e.currentTarget.checked ? { ...doc, detect: { all: [] } as Condition } : rest);
                  }}
                />
                {doc.detect && <ConditionBuilder value={doc.detect} onChange={(detect) => setDoc({ ...doc, detect })} paths={detectPaths} />}
                {catchesAll && (
                  <Alert color="yellow" p="xs">
                    Without sources and without a condition this mapping catches every event that no earlier mapping caught.
                  </Alert>
                )}
                <Group grow align="start">
                  <TextInput
                    label="Split a list into events (splitOn)"
                    description="e.g. alerts for Alertmanager; empty = one event"
                    value={doc.splitOn ?? ""}
                    onChange={(e) => setDoc({ ...doc, splitOn: e.currentTarget.value || null })}
                  />
                  <TextInput
                    label="Event type from a field (schemaFrom)"
                    description="e.g. meta.schema; overrides the mapping id"
                    value={doc.schemaFrom ?? ""}
                    onChange={(e) => setDoc({ ...doc, schemaFrom: e.currentTarget.value || null })}
                  />
                </Group>
              </Stack>
            </Card>

            <Card withBorder>
              <Title order={4}>2. Fields</Title>
              <Text size="xs" c="dimmed" mb="xs">
                Pick a row with the radio button, then click a key in the sample on the right. Empty rows get defaults (severity of the source, status firing, …).
              </Text>
              <Radio.Group value={target} onChange={setTarget}>
                <Stack gap="xs">
                  {MAPPED_FIELDS.map((f) => (
                    <FieldRow key={f.name} name={f.name} help={f.help} spec={specs[f.name]} onChange={(s) => setSpec(f.name, s)} preview={letter?.[f.name]} sample={treeValue} />
                  ))}
                </Stack>
              </Radio.Group>
            </Card>

            <Card withBorder>
              <Stack gap="xs">
                {problems.length > 0 && (
                  <Alert color="yellow" title="Before saving">
                    {problems.join(" · ")}
                  </Alert>
                )}
                <ErrorAlert error={save.error ?? remove.error} title="Not saved" />
                {save.isSuccess && <Badge color="green">Saved — applies to the next event</Badge>}
                <Group justify="space-between">
                  <Group>
                    <Button onClick={() => save.mutate()} loading={save.isPending} disabled={problems.length > 0}>
                      {isNew ? "Create mapping" : "Save changes"}
                    </Button>
                    {!isNew && sample && (
                      <Button variant="light" onClick={() => navigate("/rules/new", { state: { sample: sample.payload, sourceId: sampleSource } })}>
                        Create rule for these events
                      </Button>
                    )}
                  </Group>
                  {!isNew && (
                    <ConfirmButton title={`Delete mapping ${initial.id}?`} message="Events of this shape become unmapped again." onConfirm={() => remove.mutate()}>
                      Delete
                    </ConfirmButton>
                  )}
                </Group>
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack style={{ position: "sticky", top: 72 }}>
            <Card withBorder>
              <Title order={4} mb="xs">
                Sample event
              </Title>
              <Stack gap="xs">
                <Textarea placeholder="Paste a JSON event or a log line" ff="monospace" autosize minRows={3} maxRows={8} value={sampleText} onChange={(e) => setSampleText(e.currentTarget.value)} />
                <Select label="Arrives from source" data={(sources.data ?? []).map((s) => s.id)} value={sampleSource || null} onChange={(s) => setSampleSource(s ?? "")} clearable placeholder="http-default" searchable />
                {treeValue !== undefined && typeof treeValue === "object" ? (
                  <Card withBorder padding="xs">
                    <Text size="xs" c="dimmed" mb={4}>
                      Click a key to use it for <b>{target}</b>
                    </Text>
                    <JsonTree value={treeValue} onPick={pick} marked={marked} />
                  </Card>
                ) : (
                  sampleText.trim() && (
                    <Text size="xs" c="dimmed">
                      The tree appears when the sample is JSON or the source parses the line (logfmt / regex).
                    </Text>
                  )
                )}
              </Stack>
            </Card>
            <Card withBorder>
              <Group justify="space-between" mb="xs">
                <Title order={4}>Result (letter)</Title>
                {preview.isFetching && <Loader size="xs" />}
                {preview.data &&
                  (preview.data.errors.some((e) => e.path === "detect") ? <Badge color="yellow">detect does not match the sample</Badge> : <Badge color="green">recognised</Badge>)}
              </Group>
              <ErrorAlert error={preview.error} />
              {preview.data?.errors.filter((e) => e.path !== "detect").map((e, i) => (
                <Text key={i} size="xs" c="red">
                  {e.path}: {e.message}
                </Text>
              ))}
              {preview.data && preview.data.letters.length > 1 && (
                <Text size="xs" c="dimmed">
                  {preview.data.letters.length} events after splitting; showing the first.
                </Text>
              )}
              {letter ? <JsonBlock value={Object.fromEntries(Object.entries(letter).filter(([k]) => k !== "payload"))} /> : <Text size="sm" c="dimmed">Paste a sample to see the result.</Text>}
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function FieldRow({ name, help, spec, onChange, preview, sample }: { name: string; help: string; spec: FieldSpec; onChange: (s: FieldSpec) => void; preview: unknown; sample: unknown }) {
  const modes = modesFor(name);
  const values = name === "$severity" ? SEVERITY_VALUES : STATUS_VALUES;
  const raw = spec.mode === "map" && spec.path ? leafPaths(sample).find((p) => p.path === spec.path)?.value : undefined;

  return (
    <Card withBorder padding="xs">
      <Group gap="xs" align="start" wrap="wrap">
        <Radio value={name} aria-label={`Clicks go to ${name}`} mt={6} />
        <div style={{ width: 130 }}>
          <Text size="sm" fw={600} ff="monospace">
            {name}
          </Text>
          <Text size="10px" c="dimmed">
            {help}
          </Text>
        </div>
        {spec.mode === "json" ? (
          <Text size="xs" c="dimmed" style={{ flex: 1 }}>
            Advanced value kept as is: <code>{JSON.stringify(spec.json)}</code>{" "}
            <Button size="compact-xs" variant="subtle" onClick={() => onChange({ ...spec, mode: "path", path: "" })}>
              Replace
            </Button>
          </Text>
        ) : (
          <>
            <Select size="xs" data={modes} value={spec.mode} allowDeselect={false} onChange={(m) => m && onChange({ ...spec, mode: m as FieldSpec["mode"] })} w={170} aria-label={`${name} mode`} />
            {spec.mode === "template" || spec.mode === "const" ? (
              <TextInput
                size="xs"
                aria-label={`${name} value`}
                placeholder={spec.mode === "template" ? "{{service}} [{{instrument}}]: {{msg}}" : "value"}
                value={spec.text}
                onChange={(e) => onChange({ ...spec, text: e.currentTarget.value })}
                style={{ flex: "1 1 200px" }}
              />
            ) : (
              <TextInput size="xs" aria-label={`${name} path`} placeholder="click a key or type a path" value={spec.path} onChange={(e) => onChange({ ...spec, path: e.currentTarget.value })} style={{ flex: "1 1 160px" }} />
            )}
            {spec.mode === "format" && <Select size="xs" data={TIME_FORMATS} value={spec.format} allowDeselect={false} onChange={(f) => f && onChange({ ...spec, format: f })} w={110} aria-label={`${name} format`} />}
          </>
        )}
        <Text size="xs" c={preview === undefined || preview === null ? "dimmed" : "teal"} style={{ flex: "1 1 120px", overflowWrap: "anywhere" }} title="value in the preview">
          → {showValue(preview)}
        </Text>
      </Group>
      {spec.mode === "map" && (
        <Stack gap={4} mt={6} pl={36}>
          <Table withRowBorders={false}>
            <Table.Tbody>
              {spec.map.map(([from, to], i) => (
                <Table.Tr key={i}>
                  <Table.Td p={2}>
                    <TextInput size="xs" placeholder="value in the event" value={from} onChange={(e) => onChange({ ...spec, map: spec.map.map((p, j) => (j === i ? [e.currentTarget.value, p[1]] : p)) })} />
                  </Table.Td>
                  <Table.Td p={2}>→</Table.Td>
                  <Table.Td p={2}>
                    <Select size="xs" data={values} value={to || null} onChange={(v) => onChange({ ...spec, map: spec.map.map((p, j) => (j === i ? [p[0], v ?? ""] : p)) })} />
                  </Table.Td>
                  <Table.Td p={2}>
                    <ActionIcon variant="subtle" color="red" aria-label="Remove value" onClick={() => onChange({ ...spec, map: spec.map.filter((_, j) => j !== i) })}>
                      ✕
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Group gap="xs">
            <Button size="compact-xs" variant="subtle" onClick={() => onChange({ ...spec, map: [...spec.map, ["", ""]] })}>
              + Value
            </Button>
            {raw !== undefined && raw !== null && typeof raw !== "object" && !spec.map.some(([k]) => k === String(raw)) && (
              <Button size="compact-xs" variant="subtle" onClick={() => onChange({ ...spec, map: [...spec.map, [String(raw), ""]] })}>
                + “{String(raw)}” from the sample
              </Button>
            )}
          </Group>
        </Stack>
      )}
    </Card>
  );
}
