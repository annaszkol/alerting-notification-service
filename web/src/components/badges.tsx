import { Badge } from "@mantine/core";

import type { AlertState, Severity } from "../api/types";

export const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "red",
  error: "orange",
  warning: "yellow",
  info: "blue",
};
const STATE_COLOR: Record<AlertState, string> = { open: "red", acknowledged: "yellow", resolved: "green" };

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge color={SEVERITY_COLOR[severity]} variant="filled" radius="sm">
      {severity}
    </Badge>
  );
}

export function StateBadge({ state }: { state: AlertState }) {
  return (
    <Badge color={STATE_COLOR[state]} variant="light" radius="sm">
      {state}
    </Badge>
  );
}
