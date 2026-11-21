// A JSON value as a tree. With `onPick`, every key is a button that hands back its path (spec §3: "klikamy pola
// w drzewie JSON"), in the backend's path syntax.
import { Code, Text, UnstyledButton } from "@mantine/core";

import { formatPath, type Segment } from "./paths";

interface Props {
  value: unknown;
  onPick?: (path: string, value: unknown) => void;
  /** highlighted paths, e.g. the ones already mapped */
  marked?: Set<string>;
}

export function JsonTree({ value, onPick, marked }: Props) {
  return (
    <div style={{ fontFamily: "var(--mantine-font-family-monospace)", fontSize: 13, overflowX: "auto" }}>
      <Node value={value} path={[]} onPick={onPick} marked={marked} />
    </div>
  );
}

function Node({ value, path, onPick, marked }: { value: unknown; path: Segment[] } & Omit<Props, "value">) {
  if (value !== null && typeof value === "object") {
    const entries: [Segment, unknown][] = Array.isArray(value)
      ? value.map((v, i) => [i, v])
      : Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span>{Array.isArray(value) ? "[]" : "{}"}</span>;
    return (
      <div style={{ paddingLeft: path.length ? 16 : 0 }}>
        {entries.map(([key, child]) => {
          const childPath = [...path, key];
          const text = formatPath(childPath);
          const isObject = child !== null && typeof child === "object";
          const label = onPick ? (
            <UnstyledButton
              onClick={() => onPick(text, child)}
              title={`Use ${text}`}
              style={{
                color: "var(--mantine-color-blue-filled)",
                textDecoration: "underline dotted",
                fontWeight: marked?.has(text) ? 700 : undefined,
                fontFamily: "inherit",
                fontSize: "inherit",
              }}
            >
              {String(key)}
            </UnstyledButton>
          ) : (
            <span style={{ color: "var(--mantine-color-dimmed)" }}>{String(key)}</span>
          );
          return (
            <div key={String(key)}>
              {label}: {isObject ? <Node value={child} path={childPath} onPick={onPick} marked={marked} /> : <Leaf value={child} />}
            </div>
          );
        })}
      </div>
    );
  }
  return <Leaf value={value} />;
}

function Leaf({ value }: { value: unknown }) {
  const color = typeof value === "string" ? "teal" : typeof value === "number" ? "orange" : "grape";
  return (
    <Text span c={color} ff="monospace" fz="inherit" style={{ overflowWrap: "anywhere" }}>
      {JSON.stringify(value)}
    </Text>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <Code block style={{ maxHeight: 360, overflow: "auto" }}>
      {JSON.stringify(value, null, 2)}
    </Code>
  );
}
