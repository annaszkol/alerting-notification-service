// One `$field` of a schema mapping (spec §3, libs/conditions/mapping.py) as a small form:
//   "labels.service"                      path
//   "{{alertname}}: {{annotations.x}}"    template (contains {{ }})
//   {const: "workflow"}                   constant
//   {from: "level", map: {WARN: "warning"}}   value map
//   {from: "ts", format: "iso8601"}       time format
// Anything else is kept as JSON.

export type FieldMode = "path" | "template" | "const" | "map" | "format" | "json";

export interface FieldSpec {
  mode: FieldMode;
  path: string; // path, or `from` for map / format
  text: string; // template or constant text
  map: [string, string][];
  format: string;
  json: unknown;
}

export const MAPPED_FIELDS = [
  { name: "$schema", help: "event type name, e.g. workflow; empty = mapping id" },
  { name: "$service", help: "which service" },
  { name: "$instance", help: "host / pod" },
  { name: "$environment", help: "prod, test, …" },
  { name: "$severity", help: "info, warning, error or critical" },
  { name: "$status", help: "firing or resolved (resolved closes the alert)" },
  { name: "$eventTime", help: "when it happened" },
  { name: "$summary", help: "one line; default = start of the payload" },
  { name: "$correlationKey", help: "same key = same problem; needed for auto-close" },
] as const;

export const TIME_FORMATS = ["iso8601", "epoch_ms", "epoch_s"];
export const SEVERITY_VALUES = ["info", "warning", "error", "critical"];
export const STATUS_VALUES = ["firing", "resolved"];

const EMPTY: FieldSpec = { mode: "path", path: "", text: "", map: [], format: "iso8601", json: null };

export function parseField(value: unknown): FieldSpec {
  if (typeof value === "string") {
    return value.includes("{{") ? { ...EMPTY, mode: "template", text: value } : { ...EMPTY, mode: "path", path: value };
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    const keys = Object.keys(v).sort().join(",");
    if (keys === "const" && (typeof v.const === "string" || typeof v.const === "number" || typeof v.const === "boolean")) {
      return { ...EMPTY, mode: "const", text: String(v.const) };
    }
    if (keys === "from,map" && typeof v.from === "string" && v.map && typeof v.map === "object") {
      return { ...EMPTY, mode: "map", path: v.from, map: Object.entries(v.map as Record<string, unknown>).map(([k, x]) => [k, String(x)]) };
    }
    if (keys === "format,from" && typeof v.from === "string" && typeof v.format === "string") {
      return { ...EMPTY, mode: "format", path: v.from, format: v.format };
    }
  }
  return { ...EMPTY, mode: "json", json: value };
}

/** The mapping value, or undefined when the field is left unset (it is then omitted from `fields`). */
export function serializeField(spec: FieldSpec): unknown {
  switch (spec.mode) {
    case "path":
      return spec.path.trim() || undefined;
    case "template":
      return spec.text.trim() ? spec.text : undefined;
    case "const":
      return spec.text.trim() ? { const: spec.text.trim() } : undefined;
    case "map":
      return spec.path.trim() ? { from: spec.path.trim(), map: Object.fromEntries(spec.map.filter(([k]) => k !== "")) } : undefined;
    case "format":
      return spec.path.trim() ? { from: spec.path.trim(), format: spec.format } : undefined;
    case "json":
      return spec.json ?? undefined;
  }
}

export function setField(fields: Record<string, unknown>, name: string, spec: FieldSpec): Record<string, unknown> {
  const value = serializeField(spec);
  const next = { ...fields };
  if (value === undefined) delete next[name];
  else next[name] = value;
  return next;
}

/** Which modes make sense for a field. */
export function modesFor(name: string): { value: FieldMode; label: string }[] {
  const base: { value: FieldMode; label: string }[] = [
    { value: "path", label: "field of the event" },
    { value: "template", label: "text with {{fields}}" },
    { value: "const", label: "fixed value" },
  ];
  if (name === "$severity" || name === "$status") base.push({ value: "map", label: "translate values" });
  if (name === "$eventTime") base.push({ value: "format", label: "time field + format" });
  return base;
}

/** What a click on a JSON tree path does to a field: set the path, or append to a template. */
export function applyPick(spec: FieldSpec, path: string): FieldSpec {
  if (spec.mode === "template") return { ...spec, text: `${spec.text}{{${path}}}` };
  if (spec.mode === "const" || spec.mode === "json") return { ...spec, mode: "path", path };
  return { ...spec, path };
}
