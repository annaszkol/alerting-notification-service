// Admin (spec §9 P3): people, teams and event sources. "New team" creates the team, its new people and their roles
// in one call (onboarding, spec §9 and §13 no. 20).
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  CopyButton,
  Group,
  Loader,
  Modal,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { useSources, useTeams, useUsers } from "../api/directory";
import { SEVERITIES, type Severity, type Source, type Team, type TeamInput, type TeamRole, type User } from "../api/types";
import { usePermissions } from "../auth/permissions";
import { ConfirmButton } from "../components/ConfirmButton";
import { ErrorAlert } from "../components/ErrorAlert";
import { E164, slugError, slugify, TIMEZONES } from "../components/format";

export function Admin() {
  const perms = usePermissions();
  const [params, setParams] = useSearchParams();
  if (perms.loading) return <Loader />;
  if (!perms.isAdmin) return <Alert color="gray">Only admins can manage people, teams and sources.</Alert>;
  return (
    <Stack>
      <Title order={2}>Admin</Title>
      <Tabs value={params.get("tab") ?? "teams"} onChange={(tab) => setParams(tab ? { tab } : {})}>
        <Tabs.List>
          <Tabs.Tab value="teams">Teams</Tabs.Tab>
          <Tabs.Tab value="users">People</Tabs.Tab>
          <Tabs.Tab value="sources">Sources</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="teams" pt="md">
          <Teams />
        </Tabs.Panel>
        <Tabs.Panel value="users" pt="md">
          <Users />
        </Tabs.Panel>
        <Tabs.Panel value="sources" pt="md">
          <Sources />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

// --- people -------------------------------------------------------------------------------------------

export function userProblems(u: User): string[] {
  const out: string[] = [];
  const id = slugError(u.id);
  if (id) out.push(`Id: ${id}`);
  if (!u.name.trim()) out.push("Name is required");
  if (u.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(u.email)) out.push("Email does not look right");
  if (u.phone && !E164.test(u.phone)) out.push("Phone in international format, e.g. +48500100200");
  return out;
}

const cleanUser = (u: User): User => ({
  id: u.id.trim(),
  name: u.name.trim(),
  email: u.email?.trim() || null,
  phone: u.phone?.trim() || null,
  timezone: u.timezone || "UTC",
  isAdmin: Boolean(u.isAdmin),
});

function Users() {
  const users = useUsers();
  const perms = usePermissions();
  const client = useQueryClient();
  const [editing, setEditing] = useState<User | null>(null);
  const [isNew, setIsNew] = useState(false);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["users"] });
    void client.invalidateQueries({ queryKey: ["teams"] });
  };
  const save = useMutation({
    mutationFn: (u: User) => (isNew ? api<User>("/users", { json: cleanUser(u) }) : api<User>(`/users/${u.id}`, { method: "PUT", json: cleanUser(u) })),
    onSuccess: () => {
      refresh();
      setEditing(null);
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/users/${id}`, { method: "DELETE" }), onSuccess: refresh });

  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Email and phone are where notifications go. Roles in teams are set on the Teams tab.
        </Text>
        <Button
          onClick={() => {
            setIsNew(true);
            save.reset();
            setEditing({ id: "", name: "", email: "", phone: "", timezone: "UTC", isAdmin: false });
          }}
        >
          New person
        </Button>
      </Group>
      <ErrorAlert error={users.error ?? remove.error} />
      {users.isLoading && <Loader />}
      {!!users.data?.length && (
        <Table.ScrollContainer minWidth={700}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Email</Table.Th>
                <Table.Th>Phone</Table.Th>
                <Table.Th>Time zone</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.data.map((u) => (
                <Table.Tr key={u.id}>
                  <Table.Td>
                    <Text fw={500}>{u.name}</Text>
                    <Text size="xs" c="dimmed">
                      {u.id} {u.isAdmin && <Badge size="xs">admin</Badge>}
                    </Text>
                  </Table.Td>
                  <Table.Td>{u.email ?? "—"}</Table.Td>
                  <Table.Td>{u.phone ?? "—"}</Table.Td>
                  <Table.Td>{u.timezone}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="end" wrap="nowrap">
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => {
                          setIsNew(false);
                          save.reset();
                          setEditing({ ...u });
                        }}
                      >
                        Edit
                      </Button>
                      {u.id !== perms.userId && (
                        <ConfirmButton size="xs" title={`Delete ${u.name}?`} message="This cannot be undone. Take them out of on-call rotations first." onConfirm={() => remove.mutate(u.id)}>
                          Delete
                        </ConfirmButton>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <Modal opened={editing !== null} onClose={() => setEditing(null)} title={isNew ? "New person" : `Edit ${editing?.name}`} centered>
        {editing && (
          <Stack gap="xs">
            <UserFields user={editing} onChange={setEditing} idEditable={isNew} />
            <Checkbox label="Admin (manages people, teams, sources and mappings)" checked={Boolean(editing.isAdmin)} disabled={editing.id === perms.userId} onChange={(e) => setEditing({ ...editing, isAdmin: e.currentTarget.checked })} />
            {userProblems(editing).length > 0 && <Text size="xs" c="orange">{userProblems(editing).join(" · ")}</Text>}
            <ErrorAlert error={save.error} title="Not saved" />
            <Group justify="end">
              <Button variant="default" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button onClick={() => save.mutate(editing)} loading={save.isPending} disabled={userProblems(editing).length > 0}>
                Save
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

function UserFields({ user, onChange, idEditable }: { user: User; onChange: (u: User) => void; idEditable: boolean }) {
  const [idTouched, setIdTouched] = useState(!idEditable);
  return (
    <>
      <TextInput label="Name" withAsterisk value={user.name} onChange={(e) => onChange({ ...user, name: e.currentTarget.value, ...(idTouched ? {} : { id: slugify(e.currentTarget.value.split(" ")[0] ?? "") }) })} />
      <TextInput
        label="Login id"
        withAsterisk
        value={user.id}
        readOnly={!idEditable}
        onChange={(e) => {
          setIdTouched(true);
          onChange({ ...user, id: e.currentTarget.value });
        }}
      />
      <TextInput label="Email" value={user.email ?? ""} onChange={(e) => onChange({ ...user, email: e.currentTarget.value })} />
      <TextInput label="Phone (SMS, calls)" placeholder="+48500100200" value={user.phone ?? ""} onChange={(e) => onChange({ ...user, phone: e.currentTarget.value })} />
      <Select label="Time zone" data={TIMEZONES} searchable value={user.timezone ?? "UTC"} allowDeselect={false} onChange={(tz) => tz && onChange({ ...user, timezone: tz })} />
    </>
  );
}

// --- teams --------------------------------------------------------------------------------------------

interface TeamDraft {
  id: string;
  name: string;
  members: { userId: string; role: TeamRole }[];
  newUsers: (User & { role: TeamRole })[];
}

export function teamInput(d: TeamDraft): TeamInput {
  return {
    id: d.id.trim(),
    name: d.name.trim(),
    members: [...d.members, ...d.newUsers.map((u) => ({ userId: u.id.trim(), role: u.role }))],
    newUsers: d.newUsers.map(({ role: _role, ...u }) => {
      void _role;
      return cleanUser(u);
    }),
  };
}

export function teamProblems(d: TeamDraft, existingUsers: string[]): string[] {
  const out: string[] = [];
  const id = slugError(d.id.trim());
  if (id) out.push(`Team id: ${id}`);
  if (!d.name.trim()) out.push("Team name is required");
  const ids = new Set(d.members.map((m) => m.userId));
  d.newUsers.forEach((u, i) => {
    for (const p of userProblems(u)) out.push(`New person ${i + 1}: ${p}`);
    if (existingUsers.includes(u.id.trim()) || ids.has(u.id.trim())) out.push(`New person ${i + 1}: login id ${u.id} already exists`);
    ids.add(u.id.trim());
  });
  return out;
}

function Teams() {
  const teams = useTeams();
  const users = useUsers();
  const client = useQueryClient();
  const [draft, setDraft] = useState<TeamDraft | null>(null);
  const [isNew, setIsNew] = useState(false);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["teams"] });
    void client.invalidateQueries({ queryKey: ["users"] });
    void client.invalidateQueries({ queryKey: ["me"] });
  };
  const save = useMutation({
    mutationFn: (d: TeamDraft) => (isNew ? api<Team>("/teams", { json: teamInput(d) }) : api<Team>(`/teams/${d.id}`, { method: "PUT", json: teamInput(d) })),
    onSuccess: () => {
      refresh();
      setDraft(null);
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/teams/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const name = (id: string) => users.data?.find((u) => u.id === id)?.name ?? id;

  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Managers edit the team’s rules, runbooks and rotation and approve steps; members see and work on the team’s alerts.
        </Text>
        <Button
          onClick={() => {
            setIsNew(true);
            save.reset();
            setDraft({ id: "", name: "", members: [], newUsers: [] });
          }}
        >
          New team
        </Button>
      </Group>
      <ErrorAlert error={teams.error ?? remove.error} />
      {teams.isLoading && <Loader />}
      {teams.data?.map((t) => (
        <Card key={t.id} withBorder padding="sm">
          <Group justify="space-between" wrap="wrap">
            <div>
              <Text fw={600}>
                {t.name}{" "}
                <Text span size="xs" c="dimmed">
                  {t.id}
                </Text>
              </Text>
              <Group gap={4} mt={4}>
                {t.members.length === 0 && <Text size="sm" c="dimmed">No members</Text>}
                {t.members.map((m) => (
                  <Badge key={m.userId} variant={m.role === "manager" ? "filled" : "light"}>
                    {name(m.userId)}
                    {m.role === "manager" ? " · manager" : ""}
                  </Badge>
                ))}
              </Group>
            </div>
            <Group gap="xs">
              <Button
                size="xs"
                variant="light"
                onClick={() => {
                  setIsNew(false);
                  save.reset();
                  setDraft({ id: t.id, name: t.name, members: t.members.map((m) => ({ ...m })), newUsers: [] });
                }}
              >
                Edit
              </Button>
              <ConfirmButton size="xs" title={`Delete team ${t.name}?`} message="This cannot be undone." onConfirm={() => remove.mutate(t.id)}>
                Delete
              </ConfirmButton>
            </Group>
          </Group>
        </Card>
      ))}
      <Modal opened={draft !== null} onClose={() => setDraft(null)} title={isNew ? "New team" : `Edit ${draft?.name}`} size="lg" centered>
        {draft && <TeamForm draft={draft} setDraft={setDraft} isNew={isNew} users={users.data ?? []} save={() => save.mutate(draft)} saving={save.isPending} error={save.error} />}
      </Modal>
    </Stack>
  );
}

function TeamForm(props: { draft: TeamDraft; setDraft: (d: TeamDraft) => void; isNew: boolean; users: User[]; save: () => void; saving: boolean; error: unknown }) {
  const { draft, setDraft, isNew, users } = props;
  const [idTouched, setIdTouched] = useState(!isNew);
  const available = users.filter((u) => !draft.members.some((m) => m.userId === u.id));
  const problems = teamProblems(draft, users.map((u) => u.id));
  const noManager = ![...draft.members, ...draft.newUsers].some((m) => m.role === "manager");
  const roleSelect = (value: TeamRole, onChange: (r: TeamRole) => void, label: string) => (
    <Select
      size="xs"
      aria-label={label}
      data={[
        { value: "member", label: "member" },
        { value: "manager", label: "manager" },
      ]}
      value={value}
      allowDeselect={false}
      onChange={(r) => r && onChange(r as TeamRole)}
      w={120}
    />
  );

  return (
    <Stack gap="xs">
      <Group grow>
        <TextInput label="Team name" withAsterisk value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value, ...(idTouched ? {} : { id: slugify(e.currentTarget.value) }) })} />
        <TextInput
          label="Team id"
          withAsterisk
          value={draft.id}
          readOnly={!isNew}
          onChange={(e) => {
            setIdTouched(true);
            setDraft({ ...draft, id: e.currentTarget.value });
          }}
        />
      </Group>
      <Text size="sm" fw={500}>
        Members
      </Text>
      {draft.members.map((m, i) => (
        <Group key={m.userId} gap="xs">
          <Text size="sm" style={{ flex: 1 }}>
            {users.find((u) => u.id === m.userId)?.name ?? m.userId}
          </Text>
          {roleSelect(m.role, (role) => setDraft({ ...draft, members: draft.members.map((x, j) => (j === i ? { ...x, role } : x)) }), `Role of ${m.userId}`)}
          <ActionIcon variant="subtle" color="red" aria-label={`Remove ${m.userId}`} onClick={() => setDraft({ ...draft, members: draft.members.filter((_, j) => j !== i) })}>
            ✕
          </ActionIcon>
        </Group>
      ))}
      <Select
        size="xs"
        placeholder="+ Add an existing person"
        data={available.map((u) => ({ value: u.id, label: `${u.name} (${u.id})` }))}
        searchable
        value={null}
        onChange={(id) => id && setDraft({ ...draft, members: [...draft.members, { userId: id, role: draft.members.length || draft.newUsers.length ? "member" : "manager" }] })}
        w={300}
      />
      {draft.newUsers.map((u, i) => (
        <Card key={i} withBorder padding="xs">
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={500}>
              New person {i + 1}
            </Text>
            <Group gap="xs">
              {roleSelect(u.role, (role) => setDraft({ ...draft, newUsers: draft.newUsers.map((x, j) => (j === i ? { ...x, role } : x)) }), `Role of new person ${i + 1}`)}
              <ActionIcon variant="subtle" color="red" aria-label={`Remove new person ${i + 1}`} onClick={() => setDraft({ ...draft, newUsers: draft.newUsers.filter((_, j) => j !== i) })}>
                ✕
              </ActionIcon>
            </Group>
          </Group>
          <UserFields user={u} onChange={(nu) => setDraft({ ...draft, newUsers: draft.newUsers.map((x, j) => (j === i ? { ...x, ...nu } : x)) })} idEditable />
        </Card>
      ))}
      <Button
        size="xs"
        variant="subtle"
        w="fit-content"
        onClick={() => setDraft({ ...draft, newUsers: [...draft.newUsers, { id: "", name: "", email: "", phone: "", timezone: "UTC", isAdmin: false, role: draft.members.length || draft.newUsers.length ? "member" : "manager" }] })}
      >
        + Create a new person in this team
      </Button>
      {problems.length > 0 && <Text size="xs" c="orange">{problems.join(" · ")}</Text>}
      {noManager && (
        <Text size="xs" c="dimmed">
          Tip: without a manager nobody but admins can change the team’s rules, runbooks and rotation.
        </Text>
      )}
      <ErrorAlert error={props.error} title="Not saved" />
      <Group justify="end">
        <Button onClick={props.save} loading={props.saving} disabled={problems.length > 0}>
          {isNew ? "Create team" : "Save team"}
        </Button>
      </Group>
    </Stack>
  );
}

// --- sources ------------------------------------------------------------------------------------------

type SourceDraft = Omit<Source, "token">;

const CONFIG_KEY: Partial<Record<Source["type"], { key: string; label: string; placeholder: string }>> = {
  kafka: { key: "topic", label: "Kafka topic", placeholder: "workflow-events" },
  logfile: { key: "path", label: "Log file path", placeholder: "/var/log/app/app.log" },
};

function Sources() {
  const sources = useSources(true);
  const client = useQueryClient();
  const [draft, setDraft] = useState<SourceDraft | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [created, setCreated] = useState<Source | null>(null);
  const refresh = () => void client.invalidateQueries({ queryKey: ["sources"] });
  const save = useMutation({
    mutationFn: (d: SourceDraft) => (isNew ? api<Source>("/sources", { json: { ...d, id: d.id.trim() } }) : api<Source>(`/sources/${d.id}`, { method: "PUT", json: d })),
    onSuccess: (saved) => {
      refresh();
      setDraft(null);
      if (isNew && saved.token) setCreated(saved);
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/sources/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (s: Source) => {
      const { token: _t, ...rest } = s;
      void _t;
      return api<Source>(`/sources/${s.id}`, { method: "PUT", json: { ...rest, enabled: !s.enabled } });
    },
    onSuccess: refresh,
  });
  const ingestUrl = (id: string) => `${window.location.protocol}//${window.location.hostname}:8080/ingest/${id}`;
  const configItem = draft ? CONFIG_KEY[draft.type] : undefined;
  const idProblem = draft ? slugError(draft.id.trim()) : null;

  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Where events come from. Rules can filter on <code>$sourceId</code>. HTTP sources get an ingest token once, when created.
        </Text>
        <Button
          onClick={() => {
            setIsNew(true);
            save.reset();
            setDraft({ id: "", type: "http", parser: "json", defaultSeverity: "info", enabled: true, config: {} });
          }}
        >
          New source
        </Button>
      </Group>
      {created && (
        <Alert color="green" title={`Source ${created.id} created`} withCloseButton onClose={() => setCreated(null)}>
          <Text size="sm">Copy the token now — it is not shown again.</Text>
          <Group gap="xs" mt={4}>
            <Code>{created.token}</Code>
            <CopyButton value={created.token ?? ""}>{({ copied, copy }) => <Button size="compact-xs" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>}</CopyButton>
          </Group>
          <Text size="xs" mt={4}>
            Send events: <Code>POST {ingestUrl(created.id)}</Code> with header <Code>X-Source-Token: &lt;token&gt;</Code>
          </Text>
        </Alert>
      )}
      <ErrorAlert error={sources.error ?? remove.error ?? toggle.error} />
      {sources.isLoading && <Loader />}
      {!!sources.data?.length && (
        <Table.ScrollContainer minWidth={700}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>On</Table.Th>
                <Table.Th>Source</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Parser</Table.Th>
                <Table.Th>Default severity</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sources.data.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>
                    <Switch aria-label={`Enable ${s.id}`} checked={s.enabled !== false} onChange={() => toggle.mutate(s)} disabled={toggle.isPending} />
                  </Table.Td>
                  <Table.Td>
                    <Text fw={500}>{s.id}</Text>
                    <Text size="xs" c="dimmed">
                      {s.type === "http" ? `POST /ingest/${s.id}` : Object.entries((s.config ?? {}) as Record<string, unknown>).map(([k, v]) => `${k}: ${String(v)}`).join(", ")}
                    </Text>
                  </Table.Td>
                  <Table.Td>{s.type}</Table.Td>
                  <Table.Td>{s.parser ?? "json"}</Table.Td>
                  <Table.Td>{s.defaultSeverity ?? "info"}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="end" wrap="nowrap">
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => {
                          const { token: _t, ...rest } = s;
                          void _t;
                          setIsNew(false);
                          save.reset();
                          setDraft(rest);
                        }}
                      >
                        Edit
                      </Button>
                      <ConfirmButton size="xs" title={`Delete source ${s.id}?`} message="Events from it are rejected from now on. This cannot be undone." onConfirm={() => remove.mutate(s.id)}>
                        Delete
                      </ConfirmButton>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <Modal opened={draft !== null} onClose={() => setDraft(null)} title={isNew ? "New source" : `Edit ${draft?.id}`} centered>
        {draft && (
          <Stack gap="xs">
            <TextInput label="Source id" withAsterisk value={draft.id} readOnly={!isNew} error={draft.id && idProblem} onChange={(e) => setDraft({ ...draft, id: e.currentTarget.value })} />
            <Select
              label="Type"
              data={[
                { value: "http", label: "HTTP (POST /ingest/<id>)" },
                { value: "kafka", label: "Kafka topic" },
                { value: "logfile", label: "Log file" },
              ]}
              value={draft.type}
              allowDeselect={false}
              onChange={(t) => t && setDraft({ ...draft, type: t as Source["type"], config: {}, parser: t === "logfile" ? "logfmt" : "json" })}
            />
            {configItem && (
              <TextInput
                label={configItem.label}
                placeholder={configItem.placeholder}
                value={String(((draft.config ?? {}) as Record<string, unknown>)[configItem.key] ?? "")}
                onChange={(e) => setDraft({ ...draft, config: { ...(draft.config ?? {}), [configItem.key]: e.currentTarget.value } as Source["config"] })}
              />
            )}
            <Select
              label="Parser"
              data={[
                { value: "json", label: "JSON" },
                { value: "logfmt", label: "logfmt (key=value lines)" },
                { value: "regex", label: "regex (named groups)" },
              ]}
              value={draft.parser ?? "json"}
              allowDeselect={false}
              onChange={(p) => p && setDraft({ ...draft, parser: p as Source["parser"] })}
            />
            <Select
              label="Severity when the event has none"
              data={SEVERITIES}
              value={draft.defaultSeverity ?? "info"}
              allowDeselect={false}
              onChange={(s) => s && setDraft({ ...draft, defaultSeverity: s as Severity })}
            />
            <ErrorAlert error={save.error} title="Not saved" />
            <Group justify="end">
              <Button variant="default" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button onClick={() => save.mutate(draft)} loading={save.isPending} disabled={Boolean(idProblem) || Boolean(configItem && !String(((draft.config ?? {}) as Record<string, unknown>)[configItem.key] ?? "").trim())}>
                Save
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
