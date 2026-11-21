// Mock login (spec §9): pick a seeded user, the API returns a JWT. Documented as a mock in the README.
import { Alert, Button, Card, Container, Group, Loader, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, setToken } from "../api/client";
import type { LoginResponse, User } from "../api/types";

export function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const users = useQuery({ queryKey: ["login-users"], queryFn: () => api<User[]>("/auth/users") });
  const login = useMutation({
    mutationFn: (userId: string) => api<LoginResponse>("/auth/login", { json: { userId } }),
    onSuccess: (response) => {
      setToken(response.token);
      onLogin(response.token);
      navigate(search.get("next") ?? "/", { replace: true });
    },
  });

  return (
    <Container size="sm" py="xl">
      <Stack>
        <Title order={2}>Sign in</Title>
        <Text c="dimmed" size="sm">
          Demo login: pick a person from the seed data. There are no passwords.
        </Text>
        {users.isLoading && <Loader />}
        {users.error && <Alert color="red">Cannot load users: {users.error.message}</Alert>}
        {login.error && <Alert color="red">Login failed: {login.error.message}</Alert>}
        <SimpleGrid cols={{ base: 1, xs: 2 }}>
          {users.data?.map((user) => (
            <Card key={user.id} withBorder padding="sm">
              <Group justify="space-between" wrap="nowrap">
                <div>
                  <Text fw={600}>{user.name}</Text>
                  <Text size="xs" c="dimmed">
                    {user.id}
                    {user.isAdmin ? " · admin" : ""}
                  </Text>
                </div>
                <Button size="xs" onClick={() => login.mutate(user.id)} loading={login.isPending && login.variables === user.id}>
                  Sign in
                </Button>
              </Group>
            </Card>
          ))}
        </SimpleGrid>
      </Stack>
    </Container>
  );
}
