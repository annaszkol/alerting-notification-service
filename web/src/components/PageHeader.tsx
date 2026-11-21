// Every page starts the same way: a title, one sentence saying what the page is for, and its main actions.
import { Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-start" wrap="wrap" gap="md" mb="lg">
      <Stack gap={4} style={{ flex: "1 1 420px" }}>
        <Title order={2}>{title}</Title>
        {description && (
          <Text c="dimmed" size="sm" maw={760}>
            {description}
          </Text>
        )}
      </Stack>
      {actions && <Group gap="sm">{actions}</Group>}
    </Group>
  );
}
