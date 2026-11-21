// "Connect a source" wizard (spec §3, §9, §13 tests 11 and 20). Replaces the raw mapping editor for the common case:
//   1. name the source (HTTP endpoint, Kafka topic or log file)
//   2. send one example event (or paste one); we wait for it live
//   3. click the fields of the example: which one is the service, the message, the severity, the problem id;
//      a preview shows what the alert will look like
//   4. done: create an alert rule for these events
// Under the hood step 3 saves a schema mapping for this source only (`sources: [id]`, no `detect`, priority 5).
import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Code,
  CopyButton,
  Grid,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Stepper,
  Text,
  Textarea,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { MappingDoc, Severity, Source, UnmappedGroup } from "../api/types";
import { SEVERITY_COLOR } from "../components/badges";
import { ErrorAlert } from "../components/ErrorAlert";
import { JsonTree } from "../components/JsonTree";
import { PageHeader } from "../components/PageHeader";
import { SOURCE_KIND } from "./Sources";

const SEVERITIES: Severity[] = ["info", "warning", "error", "critical"];

type Role = "service" | "summary" | "severity" | "problem";

const QUESTIONS: { role: Role; question: string; hint: string; optional?: boolean }[] = [
  { role: "service", question: "Which field names the service or system?", hint: "e.g. payments-api, order-settlement" },
  { role: "summary", question: "Which field is the message?", hint: "the line people read in the alert" },
  { role: "severity", question: "Which field says how serious it is?", hint: "e.g. level, severity, priority", optional: true },
  { role: "problem", question: "Which field identifies the same problem?", hint: "repeats of it are grouped into one alert, e.g. an order or workflow id", optional: true },
];

export function guessSeverity(value: unknown): Severity {
  const v = String(value).toLowerCase();
  if (/crit|fatal|emerg|alert|p1/.test(v)) return "critical";
  if (/err|fail|p2/.test(v)) return "error";
  if (/warn|p3/.test(v)) return "warning";
  return "info";
}

/** The mapping `fields` for the picked paths. */
export function fieldsFor(picks: Partial<Record<Role, string>>, severityMap: [string, Severity][], fixedSeverity: Severity | null) {
  const fields: Record<string, unknown> = {};
  if (picks.service) fields.$service = picks.service;
  if (picks.summary) fields.$summary = `{{${picks.summary}}}`;
  if (picks.severity) fields.$severity = { from: picks.severity, map: Object.fromEntries(severityMap.filter(([k]) => k !== "")) };
  else if (fixedSeverity) fields.$severity = { const: fixedSeverity };
  if (picks.problem) fields.$correlationKey = `{{${picks.problem}}}`;
  return fields;
}

function slug(text: string) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function ConnectSource() {
  const { id: existingId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const initialSample = (location.state as { sample?: unknown } | null)?.sample;

  const [step, setStep] = useState(existingId ? (initialSample !== undefined ? 2 : 1) : 0);
  const [sourceId, setSourceId] = useState(existingId ?? "");
  const [sample, setSample] = useState<unknown>(initialSample);

  return (
    <Stack gap={0}>
      <PageHeader
        title={existingId ? `Set up ${existingId}` : "Connect a source"}
        description="Four short steps: name the source, send one example event, show us what its fields mean, then decide who gets alerted."
      />
      <Stepper active={step} onStepClick={(s) => s < step && setStep(s)} allowNextStepsSelect={false} size="sm">
        <Stepper.Step label="Name" description="Where events come from">
          <NameStep
            onCreated={(id) => {
              setSourceId(id);
              setStep(1);
            }}
          />
        </Stepper.Step>
        <Stepper.Step label="Example" description="Send one event">
          <ExampleStep
            sourceId={sourceId}
            onSample={(s) => {
              setSample(s);
              setStep(2);
            }}
          />
        </Stepper.Step>
        <Stepper.Step label="Meaning" description="Click the fields">
          {sample !== undefined && (
            <MeaningStep
              sourceId={sourceId}
              sample={sample}
              onSaved={() => {
                client.invalidateQueries({ queryKey: ["mappings"] });
                client.invalidateQueries({ queryKey: ["unmapped"] });
                setStep(3);
              }}
            />
          )}
        </Stepper.Step>
        <Stepper.Completed>
          <Card withBorder padding="xl" mt="md">
            <Stack align="flex-start">
              <Text size="lg" fw={600}>
                ✓ The system now understands events from {sourceId}
              </Text>
              <Text c="dimmed">
                New events from this source get a service, a severity and a message. To turn them into alerts for people, create a rule: <i>when … then notify …</i>
              </Text>
              <Group>
                <Button onClick={() => navigate("/rules/new", { state: { sample, sourceId } })}>Create an alert rule</Button>
                <Button variant="default" onClick={() => navigate("/sources")}>
                  Back to sources
                </Button>
              </Group>
            </Stack>
          </Card>
        </Stepper.Completed>
      </Stepper>
    </Stack>
  );
}

// --- step 1 ----------------------------------------------------------------------------------------------

function NameStep({ onCreated }: { onCreated: (id: string) => void }) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState<Source["type"]>("http");
  const [where, setWhere] = useState("");
  const [lineFormat, setLineFormat] = useState<"json" | "logfmt">("logfmt");
  const [token, setToken] = useState<string | null>(null);
  const id = slug(name);

  const create = useMutation({
    mutationFn: () =>
      api<Source>("/sources", {
        method: "POST",
        json: {
          id,
          type,
          parser: type === "logfile" ? lineFormat : "json",
          config: type === "kafka" ? { topic: where } : type === "logfile" ? { path: where } : {},
        },
      }),
    onSuccess: (source) => {
      client.invalidateQueries({ queryKey: ["sources"] });
      if (source.token) setToken(source.token);
      else onCreated(source.id);
    },
  });

  if (token) {
    const ingest = `${window.location.protocol}//${window.location.hostname}:8080/ingest/${id}`;
    const curl = `curl -X POST ${ingest} \\\n  -H "X-Source-Token: ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"service": "my-app", "level": "ERROR", "message": "Something failed"}'`;
    return (
      <Card withBorder padding="lg" mt="md">
        <Stack>
          <Text fw={600}>Source created. Your app sends events like this:</Text>
          <Code block>{curl}</Code>
          <Group>
            <CopyButton value={curl.replace(/\\\n\s*/g, "")}>
              {({ copied, copy }) => (
                <Button variant="light" onClick={copy}>
                  {copied ? "Copied" : "Copy command"}
                </Button>
              )}
            </CopyButton>
            <CopyButton value={token}>
              {({ copied, copy }) => (
                <Button variant="subtle" onClick={copy}>
                  {copied ? "Copied" : "Copy token only"}
                </Button>
              )}
            </CopyButton>
          </Group>
          <Alert color="yellow" variant="light">
            Save the token now: it is shown only once. Any JSON body works; you will tell us what its fields mean in a moment.
          </Alert>
          <Group>
            <Button onClick={() => onCreated(id)}>Next: send an example</Button>
          </Group>
        </Stack>
      </Card>
    );
  }

  return (
    <Card withBorder padding="lg" mt="md" maw={640}>
      <Stack>
        <TextInput
          label="Name"
          description={id && id !== name ? `Saved as "${id}"` : "Short and recognisable, e.g. billing-service"}
          placeholder="billing-service"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          data-autofocus
        />
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            How does it send events?
          </Text>
          <SegmentedControl
            value={type}
            onChange={(v) => setType(v as Source["type"])}
            data={[
              { value: "http", label: "HTTP (recommended)" },
              { value: "kafka", label: "Kafka topic" },
              { value: "logfile", label: "Log file" },
            ]}
          />
        </Stack>
        {type === "kafka" && <TextInput label="Topic" placeholder="billing-events" value={where} onChange={(e) => setWhere(e.currentTarget.value)} />}
        {type === "logfile" && (
          <>
            <TextInput label="File path" placeholder="/var/log/billing/app.log" value={where} onChange={(e) => setWhere(e.currentTarget.value)} />
            <Select
              label="Line format"
              value={lineFormat}
              onChange={(v) => setLineFormat((v as "json" | "logfmt") ?? "logfmt")}
              data={[
                { value: "logfmt", label: "key=value pairs (level=WARN msg=...)" },
                { value: "json", label: "one JSON object per line" },
              ]}
            />
          </>
        )}
        <ErrorAlert error={create.error} />
        <Group>
          <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!id || (type !== "http" && !where.trim())}>
            Create source
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

// --- step 2 ----------------------------------------------------------------------------------------------

function ExampleStep({ sourceId, onSample }: { sourceId: string; onSample: (sample: unknown) => void }) {
  const [pasted, setPasted] = useState("");
  const [pasteError, setPasteError] = useState<string | null>(null);
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/sources") });
  const source = sources.data?.find((s) => s.id === sourceId);
  const unmapped = useQuery({ queryKey: ["unmapped"], queryFn: () => api<UnmappedGroup[]>("/unmapped"), refetchInterval: 2_000 });
  const arrived = unmapped.data?.find((g) => g.sourceId === sourceId);

  return (
    <Grid mt="md" gutter="lg">
      <Grid.Col span={{ base: 12, md: 6 }}>
        <Card withBorder padding="lg" h="100%">
          <Stack>
            <Text fw={600}>Send one real event from {sourceId}</Text>
            <Text size="sm" c="dimmed">
              {source ? SOURCE_KIND[source.type] : ""}
              {source?.type === "kafka" ? `: publish a message to "${String(source.config?.topic ?? "")}".` : ""}
              {source?.type === "logfile" ? `: append a line to ${String(source.config?.path ?? "")}.` : ""}
              {source?.type === "http" ? ": run the command from the previous step, or let your app send something." : ""}
            </Text>
            {arrived ? (
              <Stack>
                <Badge color="green" size="lg" variant="light">
                  Event received
                </Badge>
                <Box mah={260} style={{ overflow: "auto" }}>
                  <JsonTree value={arrived.sample} />
                </Box>
                <Button onClick={() => onSample(arrived.sample)}>Use this event</Button>
              </Stack>
            ) : (
              <Group gap="sm">
                <Loader size="sm" type="dots" />
                <Text size="sm">Waiting for the first event…</Text>
              </Group>
            )}
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 6 }}>
        <Card withBorder padding="lg" h="100%">
          <Stack>
            <Text fw={600}>…or paste an example</Text>
            <Textarea
              autosize
              minRows={8}
              maxRows={16}
              placeholder={'{"service": "billing", "level": "ERROR", "message": "Invoice export failed"}'}
              value={pasted}
              onChange={(e) => setPasted(e.currentTarget.value)}
              styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            />
            {pasteError && <Text size="sm" c="red">{pasteError}</Text>}
            <Group>
              <Button
                variant="light"
                disabled={!pasted.trim()}
                onClick={() => {
                  try {
                    onSample(JSON.parse(pasted));
                  } catch {
                    setPasteError("This is not valid JSON.");
                  }
                }}
              >
                Use this example
              </Button>
            </Group>
          </Stack>
        </Card>
      </Grid.Col>
    </Grid>
  );
}

// --- step 3 ----------------------------------------------------------------------------------------------

interface Preview {
  letters: Record<string, unknown>[];
  errors: { path: string; message: string }[];
}

function MeaningStep({ sourceId, sample, onSaved }: { sourceId: string; sample: unknown; onSaved: () => void }) {
  const mappingId = `${sourceId}-format`;
  const existing = useQuery({ queryKey: ["mappings"], queryFn: () => api<MappingDoc[]>("/mappings") });
  const [picks, setPicks] = useState<Partial<Record<Role, string>>>({});
  const [active, setActive] = useState<Role>("service");
  const [severityMap, setSeverityMap] = useState<[string, Severity][]>([]);
  const [fixedSeverity, setFixedSeverity] = useState<Severity | null>(null);

  // prefill from a format saved earlier for this source
  useEffect(() => {
    const doc = existing.data?.find((m) => m.id === mappingId);
    if (!doc) return;
    const f = doc.fields as Record<string, unknown>;
    const unwrap = (v: unknown) => (typeof v === "string" ? v.replace(/^\{\{(.*)\}\}$/, "$1") : undefined);
    const sev = f.$severity as { from?: string; map?: Record<string, Severity>; const?: Severity } | undefined;
    setPicks({ service: unwrap(f.$service), summary: unwrap(f.$summary), problem: unwrap(f.$correlationKey), severity: sev?.from });
    if (sev?.map) setSeverityMap(Object.entries(sev.map));
    if (sev?.const) setFixedSeverity(sev.const);
  }, [existing.data, mappingId]);

  const pick = (path: string, value: unknown) => {
    if (value !== null && typeof value === "object") return; // only plain values
    setPicks((p) => ({ ...p, [active]: path }));
    if (active === "severity") {
      setSeverityMap((m) => (m.some(([k]) => k === String(value)) ? m : [[String(value), guessSeverity(value)], ...m]));
      setFixedSeverity(null);
    }
    const next = QUESTIONS.findIndex((q) => q.role === active) + 1;
    if (next < QUESTIONS.length) setActive(QUESTIONS[next].role);
  };

  const fields = useMemo(() => fieldsFor(picks, severityMap, fixedSeverity), [picks, severityMap, fixedSeverity]);
  const mapping: MappingDoc = { id: mappingId, priority: 5, sources: [sourceId], fields };
  const [debounced] = useDebouncedValue(JSON.stringify(mapping), 300);
  const preview = useQuery({
    queryKey: ["mapping-preview", debounced, sample],
    queryFn: () => api<Preview>("/mappings/preview", { method: "POST", json: { mapping: JSON.parse(debounced), sample: { sourceId, payload: sample } } }),
  });
  const letter = preview.data?.letters[0];

  const save = useMutation({
    mutationFn: async () => {
      try {
        return await api<MappingDoc>("/mappings", { method: "POST", json: mapping });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) return api<MappingDoc>(`/mappings/${mappingId}`, { method: "PUT", json: mapping });
        throw e;
      }
    },
    onSuccess: onSaved,
  });

  const marked = new Set(Object.values(picks).filter(Boolean) as string[]);

  return (
    <Grid mt="md" gutter="lg">
      <Grid.Col span={{ base: 12, md: 5 }}>
        <Card withBorder padding="lg">
          <Stack gap="xs">
            <Text fw={600}>Your example event</Text>
            <Text size="sm" c="dimmed">
              Click a field to answer the highlighted question.
            </Text>
            <Box mah={480} style={{ overflow: "auto" }}>
              <JsonTree value={sample} onPick={pick} marked={marked} />
            </Box>
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 7 }}>
        <Stack>
          {QUESTIONS.map((q) => (
            <UnstyledButton key={q.role} onClick={() => setActive(q.role)}>
              <Paper
                withBorder
                p="md"
                style={{
                  borderColor: active === q.role ? "var(--mantine-primary-color-filled)" : undefined,
                  borderWidth: active === q.role ? 2 : 1,
                }}
              >
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <Stack gap={2}>
                    <Text fw={500}>
                      {q.question} {q.optional && <Text span c="dimmed" size="sm">(optional)</Text>}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {q.hint}
                    </Text>
                  </Stack>
                  {picks[q.role] ? (
                    <Group gap={4} wrap="nowrap">
                      <Badge variant="light" size="lg" tt="none">
                        {picks[q.role]}
                      </Badge>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="gray"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPicks((p) => ({ ...p, [q.role]: undefined }));
                          if (q.role === "severity") setSeverityMap([]);
                        }}
                      >
                        ✕
                      </Button>
                    </Group>
                  ) : (
                    <Text size="sm" c={active === q.role ? "var(--mantine-primary-color-filled)" : "dimmed"}>
                      {active === q.role ? "← click a field" : "not set"}
                    </Text>
                  )}
                </Group>
                {q.role === "severity" && picks.severity && (
                  <Stack gap={6} mt="sm" onClick={(e) => e.stopPropagation()}>
                    <Text size="xs" c="dimmed">
                      What each value means (other values count as info):
                    </Text>
                    {severityMap.map(([raw, sev], i) => (
                      <Group key={i} gap="xs" wrap="nowrap">
                        <TextInput
                          size="xs"
                          value={raw}
                          w={160}
                          onChange={(e) => {
                            const v = e.currentTarget.value;
                            setSeverityMap((m) => m.map((row, j) => (j === i ? [v, row[1]] : row)));
                          }}
                        />
                        <Text size="xs">means</Text>
                        <SeveritySelect value={sev} onChange={(s) => setSeverityMap((m) => m.map((row, j) => (j === i ? [row[0], s] : row)))} />
                      </Group>
                    ))}
                    <Group>
                      <Button size="compact-xs" variant="subtle" onClick={() => setSeverityMap((m) => [...m, ["", "warning"]])}>
                        + another value
                      </Button>
                    </Group>
                  </Stack>
                )}
                {q.role === "severity" && !picks.severity && (
                  <Group gap="xs" mt="sm" onClick={(e) => e.stopPropagation()}>
                    <Text size="xs" c="dimmed">
                      No such field? Treat every event as
                    </Text>
                    <SeveritySelect value={fixedSeverity ?? "info"} onChange={setFixedSeverity} />
                  </Group>
                )}
              </Paper>
            </UnstyledButton>
          ))}

          <Card withBorder padding="lg" bg="var(--mantine-color-default-hover)">
            <Text size="xs" tt="uppercase" fw={700} c="dimmed" mb="xs">
              Preview: how the alert will read
            </Text>
            {letter ? (
              <Stack gap={6}>
                <Group gap="xs">
                  <Badge color={SEVERITY_COLOR[(letter.$severity as Severity) ?? "info"]}>{String(letter.$severity ?? "info")}</Badge>
                  <Text fw={600}>{String(letter.$service ?? "unknown service")}</Text>
                </Group>
                <Text>{String(letter.$summary ?? "(no message)")}</Text>
                {Boolean(letter.$correlationKey) && (
                  <Text size="xs" c="dimmed">
                    Repeats with the same “{String(letter.$correlationKey)}” are grouped into one alert.
                  </Text>
                )}
              </Stack>
            ) : (
              <Text size="sm" c="dimmed">
                {preview.isFetching ? "…" : "Pick a field to see the preview."}
              </Text>
            )}
          </Card>
          <ErrorAlert error={save.error} />
          <Group>
            <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!picks.service || !picks.summary}>
              Save format
            </Button>
            {(!picks.service || !picks.summary) && (
              <Text size="sm" c="dimmed">
                Pick at least the service and the message.
              </Text>
            )}
          </Group>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

function SeveritySelect({ value, onChange }: { value: Severity; onChange: (s: Severity) => void }) {
  return (
    <Select
      size="xs"
      w={130}
      value={value}
      allowDeselect={false}
      onChange={(v) => v && onChange(v as Severity)}
      data={SEVERITIES.map((s) => ({ value: s, label: s }))}
    />
  );
}
