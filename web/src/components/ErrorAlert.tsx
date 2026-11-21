// One way to show API errors everywhere: the message, the per-field list for 422, and a plain explanation for
// the codes people hit in practice (403 without the role, 404/405 for a feature the server does not have yet).
import { Alert, List } from "@mantine/core";

import { ApiError } from "../api/client";
import type { FieldError } from "../api/types";

export function fieldErrors(error: unknown): FieldError[] {
  if (error instanceof ApiError && Array.isArray(error.details)) {
    return (error.details as FieldError[]).filter((d) => d && typeof d.message === "string");
  }
  return [];
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return `You do not have permission for this: ${error.message}`;
    if (error.status === 405 || (error.status === 404 && error.code === "http_404")) {
      return "This feature is not available on the server yet.";
    }
    if (error.status === 409) return `Not possible right now: ${error.message}`;
    if (error.status === 422) return error.message || "Some fields are not valid.";
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const details = fieldErrors(error);
  return (
    <Alert color="red" title={title}>
      {errorText(error)}
      {details.length > 0 && (
        <List size="sm" mt={4}>
          {details.map((d, i) => (
            <List.Item key={i}>
              <code>{d.path}</code>: {d.message}
            </List.Item>
          ))}
        </List>
      )}
    </Alert>
  );
}
