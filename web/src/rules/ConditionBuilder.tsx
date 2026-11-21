// Condition form (spec §9 P1 "formularz AND/OR"): groups all / any / not nested freely, leaf = field + operator +
// value. Field suggestions come from the sample event (payload paths, `labels["a.b"]` for dotted keys) and the
// letter `$` fields, including `$sourceId`.
import { ActionIcon, Autocomplete, Badge, Button, Card, Group, SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Condition } from "../api/types";
import {
  conditionProblems,
  group,
  leaf,
  negate,
  OPERATOR_LABEL,
  OPERATORS,
  parseCondition,
  serializeCondition,
  updateNode,
  valueKind,
  type CondNode,
  type Operator,
} from "./condition";

interface Props {
  value: Condition | undefined;
  onChange: (value: Condition) => void;
  /** field paths to suggest */
  paths: string[];
  readOnly?: boolean;
}

export function ConditionBuilder({ value, onChange, paths, readOnly }: Props) {
  const [root, setRoot] = useState<CondNode>(() => rootOf(value));
  const emitted = useRef<string>(JSON.stringify(value ?? null));

  // A new value from outside (rule loaded, another editor) replaces the tree; our own echoes do not.
  useEffect(() => {
    const text = JSON.stringify(value ?? null);
    if (text !== emitted.current) {
      emitted.current = text;
      setRoot(rootOf(value));
    }
  }, [value]);

  const problems = useMemo(() => new Map(conditionProblems(root).map((p) => [p.nodeId, p.message])), [root]);

  const edit = (id: number, replace: (n: CondNode) => CondNode | null) => {
    const next = updateNode(root, id, replace) ?? group("all");
    setRoot(next);
    const json = serializeCondition(next);
    emitted.current = JSON.stringify(json);
    onChange(json);
  };

  const ctx: Ctx = { edit, paths, problems, readOnly: Boolean(readOnly) };
  return <NodeView node={root} ctx={ctx} isRoot />;
}

/** The form always shows a group at the top; a bare leaf is shown inside an ALL group (same meaning). */
function rootOf(value: Condition | undefined): CondNode {
  const node = parseCondition(value ?? { all: [] });
  return node.kind === "group" ? node : group("all", [node]);
}

interface Ctx {
  edit: (id: number, replace: (n: CondNode) => CondNode | null) => void;
  paths: string[];
  problems: Map<number, string>;
  readOnly: boolean;
}

function NodeView({ node, ctx, isRoot }: { node: CondNode; ctx: Ctx; isRoot?: boolean }) {
  switch (node.kind) {
    case "group":
      return <GroupView node={node} ctx={ctx} isRoot={isRoot} />;
    case "not":
      return (
        <Card withBorder padding="xs" style={{ borderColor: "var(--mantine-color-red-4)" }}>
          <Group justify="space-between" mb={6}>
            <Badge color="red" variant="light">
              NOT (must not hold)
            </Badge>
            {!ctx.readOnly && (
              <Button size="compact-xs" variant="subtle" onClick={() => ctx.edit(node.id, (n) => (n.kind === "not" ? n.child : n))}>
                Remove NOT
              </Button>
            )}
          </Group>
          <NodeView node={node.child} ctx={ctx} />
        </Card>
      );
    case "leaf":
      return <LeafView node={node} ctx={ctx} />;
    case "raw":
      return <RawView node={node} ctx={ctx} />;
  }
}

function GroupView({ node, ctx, isRoot }: { node: Extract<CondNode, { kind: "group" }>; ctx: Ctx; isRoot?: boolean }) {
  const problem = ctx.problems.get(node.id);
  return (
    <Card withBorder padding="xs" bg={isRoot ? undefined : "var(--mantine-color-default-hover)"}>
      <Group justify="space-between" mb={6} wrap="wrap">
        <Group gap="xs">
          <SegmentedControl
            size="xs"
            disabled={ctx.readOnly}
            value={node.op}
            onChange={(op) => ctx.edit(node.id, (n) => (n.kind === "group" ? { ...n, op: op as "all" | "any" } : n))}
            data={[
              { value: "all", label: "ALL of these (AND)" },
              { value: "any", label: "ANY of these (OR)" },
            ]}
          />
          {problem && (
            <Text size="xs" c="red">
              {problem}
            </Text>
          )}
        </Group>
        {!ctx.readOnly && (
          <Group gap={4}>
            <Button size="compact-xs" variant="light" onClick={() => ctx.edit(node.id, (n) => (n.kind === "group" ? { ...n, children: [...n.children, leaf()] } : n))}>
              + Condition
            </Button>
            <Button size="compact-xs" variant="light" onClick={() => ctx.edit(node.id, (n) => (n.kind === "group" ? { ...n, children: [...n.children, group(n.op === "all" ? "any" : "all", [leaf()])] } : n))}>
              + Group
            </Button>
            {!isRoot && (
              <>
                <NotButton id={node.id} ctx={ctx} />
                <ActionIcon variant="subtle" color="red" aria-label="Remove group" onClick={() => ctx.edit(node.id, () => null)}>
                  ✕
                </ActionIcon>
              </>
            )}
          </Group>
        )}
      </Group>
      <Stack gap={6}>
        {node.children.map((child) => (
          <NodeView key={child.id} node={child} ctx={ctx} />
        ))}
        {node.children.length === 0 && (
          <Text size="sm" c="dimmed">
            No conditions yet. Add one with “+ Condition”.
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function NotButton({ id, ctx }: { id: number; ctx: Ctx }) {
  return (
    <Button size="compact-xs" variant="subtle" color="red" title="Invert: must NOT hold" onClick={() => ctx.edit(id, (n) => negate(n))}>
      NOT
    </Button>
  );
}

const OP_DATA = OPERATORS.map((op) => ({ value: op, label: OPERATOR_LABEL[op] }));

function LeafView({ node, ctx }: { node: Extract<CondNode, { kind: "leaf" }>; ctx: Ctx }) {
  const problem = ctx.problems.get(node.id);
  const set = (patch: Partial<{ path: string; op: Operator; text: string }>) =>
    ctx.edit(node.id, (n) => (n.kind === "leaf" ? { ...n, ...patch } : n));
  const placeholder =
    node.op === "in" || node.op === "not_in" ? "A, B, C" : node.op === "glob" ? "trade.rejected.*" : node.op === "gt" || node.op === "gte" || node.op === "lt" || node.op === "lte" ? "10000000 or warning" : "value";

  return (
    <Group gap={6} align="start" wrap="wrap">
      <Autocomplete
        size="xs"
        placeholder="field, e.g. $severity or error.code"
        aria-label="Field"
        data={ctx.paths}
        limit={30}
        value={node.path}
        onChange={(path) => set({ path })}
        error={problem === "Choose a field" ? problem : undefined}
        readOnly={ctx.readOnly}
        style={{ flex: "2 1 200px" }}
      />
      <Select
        size="xs"
        aria-label="Operator"
        data={OP_DATA}
        value={node.op}
        allowDeselect={false}
        onChange={(op) => op && set({ op: op as Operator, text: op === "exists" ? "true" : node.op === "exists" ? "" : node.text })}
        readOnly={ctx.readOnly}
        style={{ flex: "1 1 150px" }}
      />
      {node.op === "exists" ? (
        <Select
          size="xs"
          aria-label="Exists"
          data={[
            { value: "true", label: "yes (field is present)" },
            { value: "false", label: "no (field is missing)" },
          ]}
          value={node.text === "false" ? "false" : "true"}
          allowDeselect={false}
          onChange={(text) => text && set({ text })}
          readOnly={ctx.readOnly}
          style={{ flex: "2 1 180px" }}
        />
      ) : (
        <TextInput
          size="xs"
          aria-label="Value"
          placeholder={placeholder}
          value={node.text}
          onChange={(e) => set({ text: e.currentTarget.value })}
          error={problem && problem !== "Choose a field" ? problem : undefined}
          rightSection={
            <Text size="9px" c="dimmed" pr={4}>
              {valueKind(node.text, node.op)}
            </Text>
          }
          rightSectionWidth={60}
          readOnly={ctx.readOnly}
          style={{ flex: "2 1 180px" }}
        />
      )}
      {!ctx.readOnly && (
        <>
          <NotButton id={node.id} ctx={ctx} />
          <ActionIcon variant="subtle" color="red" aria-label="Remove condition" onClick={() => ctx.edit(node.id, () => null)} mt={2}>
            ✕
          </ActionIcon>
        </>
      )}
    </Group>
  );
}

function RawView({ node, ctx }: { node: Extract<CondNode, { kind: "raw" }>; ctx: Ctx }) {
  const [text, setText] = useState(JSON.stringify(node.json, null, 2));
  const [error, setError] = useState<string | null>(null);
  return (
    <Textarea
      label="Advanced condition (JSON) — the form cannot show this part"
      ff="monospace"
      autosize
      minRows={2}
      value={text}
      readOnly={ctx.readOnly}
      error={error}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={() => {
        try {
          const json = JSON.parse(text);
          setError(null);
          ctx.edit(node.id, () => parseCondition(json));
        } catch {
          setError("Not valid JSON");
        }
      }}
    />
  );
}
