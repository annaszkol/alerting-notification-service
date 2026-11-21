import { Alert, AppShell, Badge, Burger, Button, Group, Loader, NavLink, Stack, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { api, getToken, LOGOUT_EVENT, queryString, setToken } from "./api/client";
import type { Approval } from "./api/types";
import { usePermissions } from "./auth/permissions";
import { LiveProvider, useLive } from "./feed/live";
import { AlertDetail } from "./pages/AlertDetail";
import { ApprovalLink } from "./pages/ApprovalLink";
import { Approvals } from "./pages/Approvals";
import { Feed } from "./pages/Feed";
import { Login } from "./pages/Login";
import { Admin } from "./pages/Admin";
import { MappingEditor } from "./pages/MappingEditor";
import { Mappings } from "./pages/Mappings";
import { Oncall } from "./pages/Oncall";
import { Preferences } from "./pages/Preferences";
import { RuleEditor } from "./pages/RuleEditor";
import { Rules } from "./pages/Rules";
import { RunbookEditor } from "./pages/RunbookEditor";
import { RunbookImport } from "./pages/RunbookImport";
import { Runbooks } from "./pages/Runbooks";
import { Suggestions } from "./pages/Suggestions";
import { Unmapped } from "./pages/Unmapped";

export function App() {
  const [token, setTokenState] = useState(getToken());
  const navigate = useNavigate();
  const client = useQueryClient();

  useEffect(() => {
    const onLogout = () => {
      setTokenState(null);
      client.clear();
      navigate("/login", { replace: true });
    };
    window.addEventListener(LOGOUT_EVENT, onLogout);
    return () => window.removeEventListener(LOGOUT_EVENT, onLogout);
  }, [navigate, client]);

  return (
    <LiveProvider token={token}>
      <Routes>
        <Route path="/login" element={<Login onLogin={setTokenState} />} />
        {/* email approve / reject link (spec §6): works without signing in */}
        <Route path="/approvals/:token" element={<ApprovalLink />} />
        <Route path="/*" element={token ? <Shell /> : <RedirectToLogin />} />
      </Routes>
    </LiveProvider>
  );
}

function RedirectToLogin() {
  const location = useLocation();
  return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
}

/** `/a/{id}`: the short link in SMS (spec §6). */
function ShortLink() {
  return <Navigate to={`/alerts/${useParams().id}`} replace />;
}

interface NavItem {
  to: string;
  label: string;
  /** shown when the path starts with one of these */
  match?: string[];
}

function navFor(isAdmin: boolean): { title?: string; items: NavItem[] }[] {
  const groups: { title?: string; items: NavItem[] }[] = [
    {
      items: [
        { to: "/", label: "Alerts", match: ["/alerts"] },
        { to: "/approvals", label: "Approvals" },
        { to: "/rules", label: "Rules" },
        { to: "/runbooks", label: "Runbooks" },
        { to: "/oncall", label: "On-call" },
        { to: "/suggestions", label: "Proposed changes" },
        { to: "/preferences", label: "My notifications" },
      ],
    },
  ];
  if (isAdmin) {
    groups.push({
      title: "Admin",
      items: [
        { to: "/unmapped", label: "Unmapped events" },
        { to: "/mappings", label: "Mappings" },
        { to: "/admin", label: "Teams, people, sources" },
      ],
    });
  }
  return groups;
}

function isActive(item: NavItem, path: string): boolean {
  if (item.to === "/") return path === "/" || path.startsWith("/alerts");
  return path === item.to || path.startsWith(`${item.to}/`) || Boolean(item.match?.some((m) => path.startsWith(m)));
}

function Shell() {
  const perms = usePermissions();
  const me = perms.me;
  const live = useLive();
  const location = useLocation();
  const [opened, { toggle, close }] = useDisclosure();
  const color = { live: "green", connecting: "yellow", offline: "red" }[live.status];
  const pending = useQuery({
    queryKey: ["approvals", "mine"],
    queryFn: () => api<Approval[]>(`/approvals${queryString({ status: "pending", mine: "true" })}`),
    refetchInterval: 15_000,
  });
  const pendingCount = pending.data?.length ?? 0;

  useEffect(() => close(), [location.pathname, close]);

  const role = perms.isAdmin ? "admin" : perms.managesAnything ? "manager" : "member";

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header px="md">
        <Group h="100%" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <Text fw={700} component={Link} to="/">
              Alerting
            </Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <Badge color={color} variant="dot" title="Live updates">
              {live.status}
            </Badge>
            <Text size="sm" visibleFrom="xs">
              {me?.user.name}
            </Text>
            {me && (
              <Badge variant="light" color={role === "admin" ? "red" : role === "manager" ? "grape" : "gray"} visibleFrom="xs" title={me.teams.map((t) => `${t.name}: ${t.role}`).join(", ") || "no team"}>
                {role}
              </Badge>
            )}
            <Button
              size="xs"
              variant="subtle"
              onClick={() => {
                setToken(null);
                window.dispatchEvent(new Event(LOGOUT_EVENT));
              }}
            >
              Sign out
            </Button>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {navFor(perms.isAdmin).map((group, i) => (
          <Stack key={i} gap={2} mb="md">
            {group.title && (
              <Text size="xs" c="dimmed" tt="uppercase" fw={700} px="sm" mt="xs">
                {group.title}
              </Text>
            )}
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                component={Link}
                to={item.to}
                label={item.label}
                active={isActive(item, location.pathname)}
                rightSection={
                  item.to === "/approvals" && pendingCount > 0 ? (
                    <Badge size="xs" color="grape" circle>
                      {pendingCount}
                    </Badge>
                  ) : undefined
                }
              />
            ))}
          </Stack>
        ))}
        {me && (
          <Text size="xs" c="dimmed" px="sm" mt="auto">
            {me.teams.length ? me.teams.map((t) => `${t.name} (${t.role})`).join(", ") : "Not in a team yet"}
          </Text>
        )}
      </AppShell.Navbar>
      <AppShell.Main>
        <div style={{ maxWidth: 1280, margin: "0 auto" }}>
          <Routes>
            <Route path="/" element={<Feed />} />
            <Route path="/alerts/:id" element={<AlertDetail />} />
            <Route path="/a/:id" element={<ShortLink />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/rules/new" element={<RuleEditor />} />
            <Route path="/rules/:id" element={<RuleEditor />} />
            <Route path="/runbooks" element={<Runbooks />} />
            <Route path="/runbooks/import" element={<RunbookImport />} />
            <Route path="/runbooks/:id/edit" element={<RunbookEditor />} />
            <Route path="/oncall" element={<Oncall />} />
            <Route path="/suggestions" element={<Suggestions />} />
            <Route path="/preferences" element={<Preferences />} />
            <Route path="/unmapped" element={<AdminOnly><Unmapped /></AdminOnly>} />
            <Route path="/mappings" element={<AdminOnly><Mappings /></AdminOnly>} />
            <Route path="/mappings/new" element={<AdminOnly><MappingEditor /></AdminOnly>} />
            <Route path="/mappings/:id" element={<AdminOnly><MappingEditor /></AdminOnly>} />
            <Route path="/admin" element={<Admin />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </AppShell.Main>
    </AppShell>
  );
}

/** Screens the API serves to admins only; others get a sentence instead of a 403 error. */
function AdminOnly({ children }: { children: ReactNode }) {
  const perms = usePermissions();
  if (perms.loading) return <Loader />;
  if (!perms.isAdmin) return <Alert color="gray">This screen is for admins.</Alert>;
  return <>{children}</>;
}
