// Runbook step editor (spec §7, manager): the canonical steps as a form. Action parameters get a form built from
// the action's paramsSchema (GET /actions). Saving clears the approval: a changed runbook must be approved again.
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  List,
  Loader,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { ActionInfo, CanonicalStep, Condition, Runbook } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { LETTER_FIELDS } from "../components/paths";
import { conditionProblems, parseCondition } from "../rules/condition";
import { ConditionBuilder } from "../rules/ConditionBuilder";
import { cleanStep, effectiveApproval, newStepId, stepProblems } from "../runbooks/steps";

export const useActions = () => useQuery({ queryKey: ["actions"], queryFn: () => api<ActionInfo[]>("/actions"), staleTime: 300_000 });

export function RunbookEditor() {
  const { id } = useParams();
  const runbook = useQuery({ queryKey: ["runbook", id], queryFn: () => api<Runbook>(`/runbooks/${id}`) });
  const actions = useActions();
  if (runbook.isLoading || actions.isLoading) return <Loader />;
  if (!runbook.data) return <ErrorAlert error={runbook.error ?? new Error("Runbook not found")} />;
  return <Form key={runbook.data.updatedAt ?? runbook.data.id} runbook={runbook.data} actions={actions.data ?? []} />;
}

function Form({ runbook, actions }: { runbook: Runbook; actions: ActionInfo[] }) {
  const perms = usePermissions();
  const navigate = useNavigate();
  const client = useQueryClient();
  const canonical = runbook.canonical as { steps?: CanonicalStep[]; match?: Condition };
  const [title, setTitle] = useState(runbook.title);
  const [match, setMatch] = useState<Condition | null>(canonical.match ?? null);
  // each step carries a stable key, so moving or removing steps keeps every form field with its own step
  const [items, setItems] = useState(() => (canonical.steps ?? []).map((s, k) => ({ k, s })));
  const [nextKey, setNextKey] = useState((canonical.steps ?? []).length);
  const steps = items.map((x) => x.s);
  const setSteps = (next: CanonicalStep[], added = false) => {
    if (added) {
      setItems([...items, { k: nextKey, s: next[next.length - 1] }]);
      setNextKey(nextKey + 1);
    } else {
      setItems(next.map((s) => ({ k: items.find((x) => x.s === s)?.k ?? -1, s })));
    }
  };

  const save = useMutation({
    mutationFn: () => api<Runbook>(`/runbooks/${runbook.id}`, { method: "PUT", json: { title, match, steps: steps.map(cleanStep) } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["runbooks"] });
      void client.invalidateQueries({ queryKey: ["runbook", runbook.id] });
      navigate("/runbooks");
    },
  });

  if (!perms.loading && !perms.isManager(runbook.owner.team)) {
    return <Alert color="gray">Only managers of team {runbook.owner.team} can change this runbook.</Alert>;
  }

  const problems = [...stepProblems(steps, actions), ...(match ? conditionProblems(parseCondition(match)).map((p) => `Match: ${p.message}`) : [])];
  if (!title.trim()) problems.unshift("Give the runbook a title");
  const setStep = (i: number, s: CanonicalStep) => setItems(items.map((x, j) => (j === i ? { ...x, s } : x)));
  const move = (i: number, d: -1 | 1) => {
    const next = [...items];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setItems(next);
  };

  return (
    <Stack>
      <Anchor component={Link} to="/runbooks" size="sm">
        ← All runbooks
      </Anchor>
      <Title order={2}>Edit runbook {runbook.id}</Title>
      {runbook.approvedBy && <Alert color="yellow">Saving removes the approval by {runbook.approvedBy}: the runbook must be approved again before its actions can run.</Alert>}
      <Card withBorder>
        <Stack gap="xs">
          <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
          <Switch label="Show on alerts matching its own condition (besides rules that link it)" checked={match !== null} onChange={(e) => setMatch(e.currentTarget.checked ? { all: [] } : null)} />
          {match !== null && <ConditionBuilder value={match} onChange={setMatch} paths={LETTER_FIELDS} />}
        </Stack>
      </Card>
      {items.map(({ k, s: step }, i) => (
        <StepCard
          key={k}
          index={i}
          step={step}
          steps={steps}
          actions={actions}
          onChange={(s) => setStep(i, s)}
          onRemove={() => setItems(items.filter((_, j) => j !== i))}
          onMove={(d) => move(i, d)}
        />
      ))}
      <Group>
        <Button variant="light" onClick={() => setSteps([...steps, { id: newStepId(steps), kind: "text", text: "" }], true)}>
          + Manual step
        </Button>
        <Button variant="light" onClick={() => setSteps([...steps, { id: newStepId(steps), kind: "action", action: actions[0]?.name, params: {} }], true)}>
          + Action
        </Button>
        <Button variant="light" onClick={() => setSteps([...steps, { id: newStepId(steps), kind: "wait", duration: "2m" }], true)}>
          + Wait
        </Button>
      </Group>
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
          <ErrorAlert error={save.error} title="Not saved" />
          <Group>
            <Button onClick={() => save.mutate()} loading={save.isPending} disabled={problems.length > 0}>
              Save runbook
            </Button>
            <Button variant="default" component={Link} to="/runbooks">
              Cancel
            </Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}

function StepCard(props: {
  index: number;
  step: CanonicalStep;
  steps: CanonicalStep[];
  actions: ActionInfo[];
  onChange: (s: CanonicalStep) => void;
  onRemove: () => void;
  onMove: (d: -1 | 1) => void;
}) {
  const { index, step, steps, actions, onChange } = props;
  const action = actions.find((a) => a.name === step.action);
  const forced = step.kind === "action" && action?.minApproval === "required";
  const effective = effectiveApproval(step, actions);
  const set = (patch: Partial<CanonicalStep>) => onChange({ ...step, ...patch });

  return (
    <Card withBorder>
      <Stack gap="xs">
        <Group justify="space-between">
          <Group gap="xs">
            <Badge>{index + 1}</Badge>
            {step.needs_review && (
              <Badge color="yellow" title="The importer did not understand this part of the original">
                needs review
              </Badge>
            )}
          </Group>
          <Group gap={4}>
            <ActionIcon variant="subtle" aria-label="Move up" disabled={index === 0} onClick={() => props.onMove(-1)}>
              ↑
            </ActionIcon>
            <ActionIcon variant="subtle" aria-label="Move down" disabled={index === steps.length - 1} onClick={() => props.onMove(1)}>
              ↓
            </ActionIcon>
            <ActionIcon variant="subtle" color="red" aria-label="Remove step" onClick={props.onRemove}>
              ✕
            </ActionIcon>
          </Group>
        </Group>
        <Group grow align="start">
          <Select
            label="Kind"
            data={[
              { value: "text", label: "Manual step (checkbox)" },
              { value: "action", label: "Action" },
              { value: "wait", label: "Wait" },
            ]}
            value={step.kind}
            allowDeselect={false}
            onChange={(kind) => kind && set({ kind: kind as CanonicalStep["kind"], ...(kind === "action" && !step.action ? { action: actions[0]?.name, params: {} } : {}) })}
          />
          <TextInput label="Step id" value={step.id} onChange={(e) => set({ id: e.currentTarget.value })} />
          <TextInput label="Title (optional)" value={step.title ?? ""} onChange={(e) => set({ title: e.currentTarget.value })} />
        </Group>
        {step.kind === "text" && <Textarea label="What to do" description="{{fields}} are filled from the alert" autosize minRows={2} value={step.text ?? ""} onChange={(e) => set({ text: e.currentTarget.value })} />}
        {step.kind === "wait" && <TextInput label="Wait for" placeholder="2m" value={step.duration ?? ""} onChange={(e) => set({ duration: e.currentTarget.value })} w={160} />}
        {step.kind === "action" && (
          <>
            <Select
              label="Action"
              description={action?.description}
              data={actions.map((a) => ({ value: a.name, label: `${a.name}${a.minApproval === "required" ? " (always needs approval)" : ""}` }))}
              value={step.action ?? null}
              allowDeselect={false}
              onChange={(name) => name && set({ action: name, params: {} })}
            />
            {action && <ParamsForm key={action.name} schema={action.paramsSchema} value={step.params ?? {}} onChange={(params) => set({ params })} />}
          </>
        )}
        <Group align="end" wrap="wrap">
          <Select
            label="Approval"
            data={[
              { value: "none", label: "not needed", disabled: forced },
              { value: "required", label: "required" },
            ]}
            value={effective}
            allowDeselect={false}
            description={forced ? "This action always needs approval" : undefined}
            onChange={(a) => a && set({ approval: a as "none" | "required" })}
            w={170}
          />
          {effective === "required" && (
            <Select
              label="Approved by"
              data={[
                { value: "manager", label: "a manager" },
                { value: "member", label: "any team member" },
              ]}
              value={step.approval_role ?? "manager"}
              allowDeselect={false}
              onChange={(r) => r && set({ approval_role: r as "manager" | "member" })}
              w={170}
            />
          )}
          {step.kind === "action" && (
            <>
              <NumberInput label="Attempts" min={1} value={step.attempts ?? 1} onChange={(v) => set({ attempts: typeof v === "number" && v > 1 ? v : undefined })} w={100} />
              <Select
                label="If it still fails, go to"
                placeholder="next step"
                data={steps.filter((s) => s.id !== step.id).map((s) => s.id)}
                value={step.on_failure ?? null}
                clearable
                onChange={(t) => set({ on_failure: t ?? undefined })}
                w={200}
              />
            </>
          )}
        </Group>
        <Group>
          {step.kind === "action" && <Checkbox label="Success resolves the alert" checked={Boolean(step.resolves)} onChange={(e) => set({ resolves: e.currentTarget.checked || undefined })} />}
          <Checkbox label="Skip if the alert is already resolved" checked={Boolean(step.only_if_open)} onChange={(e) => set({ only_if_open: e.currentTarget.checked || undefined })} />
          {step.needs_review && <Checkbox label="Reviewed" checked={false} onChange={() => set({ needs_review: undefined })} />}
        </Group>
      </Stack>
    </Card>
  );
}

interface JsonSchema {
  required?: string[];
  properties?: Record<string, { type?: string; title?: string; description?: string; items?: { enum?: string[] } }>;
}

/** Form from an action's JSON Schema: strings are templates, objects and arrays are edited as JSON. */
export function ParamsForm({ schema, value, onChange }: { schema: unknown; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const s = (schema ?? {}) as JsonSchema;
  const required = new Set(s.required ?? []);
  const set = (key: string, v: unknown) => {
    const next = { ...value };
    if (v === undefined || v === "") delete next[key];
    else next[key] = v;
    onChange(next);
  };
  const entries = Object.entries(s.properties ?? {});
  if (entries.length === 0) return <Text size="xs" c="dimmed">This action has no parameters.</Text>;
  return (
    <Stack gap={6} pl="sm">
      {entries.map(([key, prop]) =>
        prop.type === "string" || !prop.type ? (
          <TextInput
            key={key}
            size="sm"
            label={prop.title ?? key}
            description={key === "url" ? "Only allowlisted hosts are called" : "Template: {{field}} is filled from the alert"}
            withAsterisk={required.has(key)}
            value={typeof value[key] === "string" ? (value[key] as string) : value[key] === undefined ? "" : JSON.stringify(value[key])}
            onChange={(e) => set(key, e.currentTarget.value)}
          />
        ) : prop.type === "array" && prop.items?.enum ? (
          <Checkbox.Group key={key} label={prop.title ?? key} value={(value[key] as string[]) ?? []} onChange={(v) => set(key, v.length ? v : undefined)}>
            <Group mt={4}>
              {prop.items.enum.map((opt) => (
                <Checkbox key={opt} value={opt} label={opt} />
              ))}
            </Group>
          </Checkbox.Group>
        ) : (
          <JsonParam key={key} label={prop.title ?? key} required={required.has(key)} value={value[key]} onChange={(v) => set(key, v)} />
        ),
      )}
    </Stack>
  );
}

function JsonParam({ label, required, value, onChange }: { label: string; required: boolean; value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(value === undefined ? "" : JSON.stringify(value));
  const [error, setError] = useState<string | null>(null);
  return (
    <TextInput
      label={label}
      description='JSON, e.g. {"oncall": "pricing"}'
      withAsterisk={required}
      ff="monospace"
      value={text}
      error={error}
      onChange={(e) => {
        const t = e.currentTarget.value;
        setText(t);
        if (!t.trim()) {
          setError(null);
          onChange(undefined);
          return;
        }
        try {
          onChange(JSON.parse(t));
          setError(null);
        } catch {
          setError("Not valid JSON yet");
        }
      }}
    />
  );
}
