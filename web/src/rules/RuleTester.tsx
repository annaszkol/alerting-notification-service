// Rule tester (spec §4 "Tester reguły w UI"): paste an event or take the first event of an existing alert and
// see the letter, the result of every condition with the actual value, the rendered title and the recipients and
// channels right now. Nothing is written (POST /rules/test).
import { Alert, Badge, Button, Card, Group, NumberInput, SegmentedControl, Stack, Table, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ConditionResult, RuleDoc, RuleTestResult } from "../api/types";
import { ErrorAlert } from "../components/ErrorAlert";
import { SLUG } from "../components/format";
import { JsonBlock } from "../components/JsonTree";
import { showValue } from "../components/paths";

export interface Sample {
  mode: "event" | "alert";
  text: string;
  sourceId: string;
  alertId: number | null;
}

export const EMPTY_SAMPLE: Sample = { mode: "event", text: "", sourceId: "", alertId: null };

/** The pasted text as a payload: JSON when it parses, otherwise a log line (string). */
export function parseSample(text: string): { payload: unknown; json: boolean } {
  try {
    return { payload: JSON.parse(text), json: true };
  } catch {
    return { payload: text.trim(), json: false };
  }
}

export function testRequest(rule: RuleDoc, sample: Sample) {
  const { updatedBy: _u, updatedAt: _t, ...rest } = rule;
  void _u;
  void _t;
  // an unsaved rule may not have an id yet; the tester only needs a valid one
  const doc = { ...rest, id: SLUG.test(rest.id.trim()) ? rest.id.trim() : "draft-rule" };
  if (sample.mode === "alert") return { rule: doc, alertId: sample.alertId };
  const { payload, json } = parseSample(sample.text);
  return {
    rule: doc,
    event: {
      payload,
      ...(sample.sourceId.trim() ? { sourceId: sample.sourceId.trim() } : {}),
      contentType: json ? "application/json" : "text/plain",
    },
  };
}

export function RuleTester({ rule, sample, onSample, blocked }: { rule: RuleDoc; sample: Sample; onSample: (s: Sample) => void; blocked?: string | null }) {
  const test = useMutation({ mutationFn: () => api<RuleTestResult>("/rules/test", { json: testRequest(rule, sample) }) });
  const parsed = sample.text.trim() ? parseSample(sample.text) : null;
  const ready = sample.mode === "alert" ? Boolean(sample.alertId) : Boolean(sample.text.trim());

  return (
    <Stack gap="sm">
      <SegmentedControl
        size="xs"
        value={sample.mode}
        onChange={(mode) => onSample({ ...sample, mode: mode as Sample["mode"] })}
        data={[
          { value: "event", label: "Paste an event" },
          { value: "alert", label: "Use an existing alert" },
        ]}
      />
      {sample.mode === "event" ? (
        <>
          <Textarea
            label="Event payload"
            description="JSON, or one log line (e.g. ts=… level=WARN service=pricing-engine-1 …)"
            ff="monospace"
            autosize
            minRows={5}
            maxRows={16}
            value={sample.text}
            onChange={(e) => onSample({ ...sample, text: e.currentTarget.value })}
          />
          {parsed && !parsed.json && (
            <Text size="xs" c="dimmed">
              Not JSON: sent as a text line, parsed by the source’s parser.
            </Text>
          )}
          <TextInput
            label="Source id (simulated)"
            description="Empty = http-default. Use the source the event really comes from, e.g. pricing-logs."
            value={sample.sourceId}
            onChange={(e) => onSample({ ...sample, sourceId: e.currentTarget.value })}
          />
        </>
      ) : (
        <NumberInput label="Alert id" description="Its first event is used" min={1} value={sample.alertId ?? ""} onChange={(v) => onSample({ ...sample, alertId: typeof v === "number" ? v : null })} />
      )}
      <Group>
        <Button onClick={() => test.mutate()} loading={test.isPending} disabled={!ready || Boolean(blocked)}>
          Test rule
        </Button>
        {blocked && (
          <Text size="xs" c="dimmed">
            Fix the form first: {blocked}
          </Text>
        )}
      </Group>
      <ErrorAlert error={test.error} />
      {test.data && <TestResult result={test.data} />}
    </Stack>
  );
}

function TestResult({ result }: { result: RuleTestResult }) {
  if (result.letters.length === 0) return <Alert color="yellow">The event produced no letter (empty batch).</Alert>;
  return (
    <Stack gap="sm">
      {result.letters.map((item, i) => (
        <Card key={i} withBorder padding="sm">
          <Stack gap="xs">
            <Group gap="xs">
              {item.matched ? <Badge color="green">Matches — an alert would be created</Badge> : <Badge color="gray">Does not match</Badge>}
              <Badge variant="outline" color="gray">
                schema: {String(item.letter.$schema)}
              </Badge>
              <Text size="xs" c="dimmed">
                mapping: {item.mappingName ?? "none (unmapped)"}
              </Text>
            </Group>
            <Title order={6}>Conditions</Title>
            <ConditionTree node={item.conditions} />
            {item.matched && (
              <>
                <Title order={6}>Alert</Title>
                <Text fw={600} style={{ overflowWrap: "anywhere" }}>
                  {item.title}
                </Text>
                {item.body && (
                  <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                    {item.body}
                  </Text>
                )}
                {!!item.fields?.length && (
                  <Table>
                    <Table.Tbody>
                      {item.fields.map((f) => (
                        <Table.Tr key={f.path}>
                          <Table.Td c="dimmed">{f.path}</Table.Td>
                          <Table.Td>{showValue(f.value)}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
                <Title order={6}>Who is notified now</Title>
                {!item.recipients?.length ? (
                  <Text size="sm" c="dimmed">
                    Nobody: UI feed only.
                  </Text>
                ) : (
                  <Table>
                    <Table.Tbody>
                      {item.recipients.map((r) => (
                        <Table.Tr key={r.userId}>
                          <Table.Td>{r.userId}</Table.Td>
                          <Table.Td>{r.channels.length ? r.channels.join(", ") : <Text span c="dimmed" size="sm">no channel right now</Text>}</Table.Td>
                          <Table.Td c="dimmed">{r.reason}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
                <Text size="xs" c="dimmed">
                  fingerprint {item.fingerprint}
                </Text>
              </>
            )}
            <details>
              <summary>
                <Text span size="sm" c="dimmed">
                  Letter (normalised event)
                </Text>
              </summary>
              <JsonBlock value={item.letter} />
            </details>
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

const OP_TEXT: Record<string, string> = { all: "ALL of", any: "ANY of", not: "NOT" };

export function ConditionTree({ node }: { node: ConditionResult }) {
  const isGroup = Boolean(node.op && OP_TEXT[node.op]);
  if (node.op === "invalid") {
    return (
      <Text size="sm" c="red">
        ✗ invalid condition (check the JSON)
      </Text>
    );
  }
  return (
    <div style={{ borderLeft: `3px solid var(--mantine-color-${node.result ? "green" : "red"}-6)`, paddingLeft: 8, marginBottom: 4 }}>
      <Text size="sm">
        <Text span fw={600} c={node.result ? "green" : "red"}>
          {node.result ? "✓" : "✗"}
        </Text>{" "}
        {isGroup ? (
          OP_TEXT[node.op!]
        ) : (
          <>
            <code>{node.path}</code> {node.op} <code>{JSON.stringify(node.expected)}</code>
            <Text span size="xs" c="dimmed">
              {" "}
              — actual: {node.missing ? "missing" : <code>{JSON.stringify(node.actual)}</code>}
            </Text>
          </>
        )}
      </Text>
      {node.children?.map((child, i) => (
        <div key={i} style={{ marginLeft: 12 }}>
          <ConditionTree node={child} />
        </div>
      ))}
    </div>
  );
}
