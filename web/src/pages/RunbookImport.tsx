// Runbook import (spec §7, §9 P2, manager): paste YAML, a numbered Markdown list, a `- [ ]` checklist, free text
// or a wiki link, preview the canonical steps (dryRun), then save. A saved runbook is text only until a manager
// approves it.
import { Alert, Anchor, Badge, Button, Card, Group, SegmentedControl, Select, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { useTeams } from "../api/directory";
import type { CanonicalStep, Runbook } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ErrorAlert } from "../components/ErrorAlert";
import { slugError, slugify } from "../components/format";
import { stepLabel } from "../runbooks/steps";

type Format = Runbook["format"];

const EXAMPLES: Record<Format, string> = {
  yaml: `runbook: settlement-timeout
match: { all: [ { type: WORKFLOW_FAILED }, { error.code: TIMEOUT } ] }
steps:
  - check: "Is counterparty-service healthy?"
    action: http_get
    url: http://counterparty-service/health
  - action: retry_workflow
    params: { workflowId: "{{workflowId}}" }
    if: fails twice
    then: escalate
  - action: escalate
    team: counterparty-support
    approval: required`,
  markdown: "1. Check the dashboard for {{$service}}\n2. Restart the pod if latency is still high\n3. Tell the desk in #rates",
  checklist: "- [ ] Check the dashboard\n- [ ] Restart the pod\n- [ ] Tell the desk",
  text: "If prices stop updating, restart pricing-engine and call the on-call quant.",
  link: "https://wiki.example.com/runbooks/pricing",
};

const FORMAT_LABEL: Record<Format, string> = { yaml: "YAML", markdown: "Numbered list", checklist: "Checklist", text: "Free text", link: "Wiki link" };

export function RunbookImport() {
  const perms = usePermissions();
  const teams = useTeams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const managed = perms.managedTeams(teams.data);

  const [format, setFormat] = useState<Format>("checklist");
  const [original, setOriginal] = useState("");
  const [title, setTitle] = useState("");
  const [id, setId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [team, setTeam] = useState<string | null>(null);
  const owner = team ?? managed[0] ?? null;

  const body = () => ({ id: id.trim(), ...(title.trim() ? { title: title.trim() } : {}), owner: { team: owner }, format, original });
  const preview = useMutation({ mutationFn: () => api<Runbook>("/runbooks/import?dryRun=true", { json: body() }) });
  const save = useMutation({
    mutationFn: () => api<Runbook>("/runbooks/import", { json: body() }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["runbooks"] });
      navigate("/runbooks");
    },
  });

  if (!perms.loading && managed.length === 0) {
    return <Alert color="gray">Only team managers can import runbooks.</Alert>;
  }
  const idProblem = slugError(id.trim());
  const ready = !idProblem && Boolean(owner) && Boolean(original.trim());
  const steps = ((preview.data?.canonical as { steps?: CanonicalStep[] } | undefined)?.steps ?? []) as CanonicalStep[];

  return (
    <Stack>
      <Anchor component={Link} to="/runbooks" size="sm">
        ← All runbooks
      </Anchor>
      <Title order={2}>Import runbook</Title>
      <Card withBorder>
        <Stack gap="xs">
          <Group grow align="start">
            <TextInput
              label="Title"
              value={title}
              onChange={(e) => {
                setTitle(e.currentTarget.value);
                if (!idTouched) setId(slugify(e.currentTarget.value));
              }}
            />
            <TextInput
              label="Id"
              value={id}
              error={id && idProblem}
              onChange={(e) => {
                setIdTouched(true);
                setId(e.currentTarget.value);
              }}
            />
            <Select label="Team" data={managed.map((t) => ({ value: t, label: teams.data?.find((x) => x.id === t)?.name ?? t }))} value={owner} allowDeselect={false} onChange={setTeam} />
          </Group>
          <SegmentedControl value={format} onChange={(f) => setFormat(f as Format)} data={(Object.keys(FORMAT_LABEL) as Format[]).map((f) => ({ value: f, label: FORMAT_LABEL[f] }))} />
          <Textarea
            label={format === "link" ? "Link" : "Runbook"}
            description={
              format === "yaml"
                ? "Fields: matches / check / action / url / approval; phrases like “fails twice” become retries. Anything unknown becomes a step to review."
                : format === "link"
                  ? "The alert shows the link"
                  : "Each line or list item becomes a manual step with a checkbox; {{fields}} are filled from the alert."
            }
            ff="monospace"
            autosize
            minRows={6}
            placeholder={EXAMPLES[format]}
            value={original}
            onChange={(e) => setOriginal(e.currentTarget.value)}
          />
          <Group>
            <Button variant="light" onClick={() => preview.mutate()} loading={preview.isPending} disabled={!ready}>
              Preview steps
            </Button>
            <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!ready}>
              Save runbook
            </Button>
            <Text size="xs" c="dimmed">
              After saving, a manager approves it on the Runbooks page; until then its actions cannot run.
            </Text>
          </Group>
          <ErrorAlert error={preview.error ?? save.error} />
        </Stack>
      </Card>
      {preview.data && (
        <Card withBorder>
          <Title order={4} mb="xs">
            Canonical steps
          </Title>
          {steps.length === 0 && <Text c="dimmed">No steps (a link runbook only shows the link).</Text>}
          <ol style={{ margin: 0, paddingLeft: "1.3rem", display: "grid", gap: 4 }}>
            {steps.map((s) => (
              <li key={s.id}>
                <Group gap={6}>
                  <Badge size="xs" variant="light">
                    {s.kind}
                  </Badge>
                  <Text size="sm">{stepLabel(s)}</Text>
                  {s.kind === "action" && <code>{s.action}</code>}
                  {s.approval === "required" && (
                    <Badge size="xs" color="grape" variant="light">
                      approval
                    </Badge>
                  )}
                  {s.attempts && s.attempts > 1 && <Badge size="xs">×{s.attempts}</Badge>}
                  {s.on_failure && <Badge size="xs" color="orange" variant="light">on failure → {s.on_failure}</Badge>}
                  {s.needs_review && <Badge size="xs" color="yellow">review: not understood</Badge>}
                </Group>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </Stack>
  );
}
