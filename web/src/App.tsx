import { AppShell, Badge, Button, Group, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { api, getToken, LOGOUT_EVENT, setToken } from "./api/client";
import type { Me } from "./api/types";
import { LiveProvider, useLive } from "./feed/live";
import { AlertDetail } from "./pages/AlertDetail";
import { Feed } from "./pages/Feed";
import { Login } from "./pages/Login";

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

function Shell() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const live = useLive();
  const color = { live: "green", connecting: "yellow", offline: "red" }[live.status];

  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header px="md">
        <Group h="100%" justify="space-between">
          <Text fw={700}>Alerting</Text>
          <Group gap="sm">
            <Badge color={color} variant="dot" title="Live updates">
              {live.status}
            </Badge>
            <Text size="sm">{me.data?.user.name}</Text>
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
      <AppShell.Main maw={1200} mx="auto">
        <Routes>
          <Route path="/" element={<Feed />} />
          <Route path="/alerts/:id" element={<AlertDetail />} />
          <Route path="/a/:id" element={<ShortLink />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
