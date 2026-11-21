// Page behind the Approve / Reject link in approval emails (spec §6). No login: the signed link names the
// person it was sent to, and the API still checks their role. Single use, valid 24 h.
import { Alert, Badge, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Approval, ApprovalByToken } from "../api/types";
import { SeverityBadge } from "../components/badges";

const GONE: Record<number, string> = {
  404: "This approval link is not valid.",
  409: "This approval has already been decided.",
  410: "This approval link has expired (links are valid for 24 hours).",
};

export function ApprovalLink() {
  const token = useParams().token ?? "";
  const page = useQuery({
    queryKey: ["approval-link", token],
    queryFn: () => api<ApprovalByToken>(`/approvals/by-token/${encodeURIComponent(token)}`),
    retry: false,
  });
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      api<Approval>(`/approvals/by-token/${encodeURIComponent(token)}`, { json: { decision } }),
  });

  const failure = page.error ?? decide.error;
  const message = failure instanceof ApiError ? (GONE[failure.status] ?? failure.message) : failure?.message;

  return (
    <Container size="sm" py="xl">
      <Stack>
        <Title order={2}>Approval request</Title>
        {page.isLoading && <Loader />}
        {message && <Alert color={failure instanceof ApiError && failure.status === 403 ? "red" : "yellow"}>{message}</Alert>}
        {decide.data && (
          <Alert color={decide.data.status === "approved" ? "green" : "red"}>
            {decide.data.status === "approved" ? "Approved" : "Rejected"} by {decide.data.decidedBy}. You can close this page.
          </Alert>
        )}
        {page.data && !decide.data && (
          <Card withBorder>
            <Stack>
              <Group gap="xs">
                <SeverityBadge severity={page.data.alert.severity} />
                <Text fw={600}>{page.data.alert.title}</Text>
              </Group>
              <div>
                <Text size="sm" c="dimmed">
                  Step waiting for approval
                </Text>
                <Text fw={600}>{page.data.step.title}</Text>
                {page.data.step.action && (
                  <Text size="sm">
                    Action <code>{page.data.step.action}</code>{" "}
                    {Object.keys(page.data.step.params ?? {}).length > 0 && <code>{JSON.stringify(page.data.step.params)}</code>}
                  </Text>
                )}
              </div>
              <Group gap="xs">
                <Badge variant="light" color="grape">
                  needs {page.data.approval.team} {page.data.approval.requiredRole}
                </Badge>
                <Text size="sm">You are deciding as {page.data.userId}.</Text>
              </Group>
              <Group>
                <Button color="grape" onClick={() => decide.mutate("approve")} loading={decide.isPending && decide.variables === "approve"}>
                  Approve
                </Button>
                <Button color="red" variant="light" onClick={() => decide.mutate("reject")} loading={decide.isPending && decide.variables === "reject"}>
                  Reject
                </Button>
              </Group>
            </Stack>
          </Card>
        )}
      </Stack>
    </Container>
  );
}
