// Paths as the backend reads them (libs/conditions/paths.py, spec §3): `a.b` nested, numeric segments index
// arrays, keys that contain a dot (or brackets, quotes, edge spaces) go in brackets: `labels["app.kubernetes.io/name"]`.

export type Segment = string | number;

const PLAIN = /^[^.[\]"'\s](?:[^.[\]"']*[^.[\]"'\s])?$/;

export function formatPath(segments: Segment[]): string {
  let out = "";
  for (const seg of segments) {
    if (typeof seg === "number") {
      out += out ? `.${seg}` : String(seg);
    } else if (PLAIN.test(seg) && !/^\d+$/.test(seg)) {
      out += out ? `.${seg}` : seg;
    } else {
      out += `["${seg.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`;
    }
  }
  return out;
}

/** Every leaf path of a JSON value with its value, depth first (used for path suggestions). */
export function leafPaths(value: unknown, prefix: Segment[] = [], out: { path: string; value: unknown }[] = []) {
  if (out.length >= 500) return out;
  if (Array.isArray(value)) {
    if (value.length === 0 && prefix.length) out.push({ path: formatPath(prefix), value });
    value.slice(0, 20).forEach((item, i) => leafPaths(item, [...prefix, i], out));
  } else if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0 && prefix.length) out.push({ path: formatPath(prefix), value });
    for (const [key, item] of entries) leafPaths(item, [...prefix, key], out);
  } else if (prefix.length) {
    out.push({ path: formatPath(prefix), value });
  }
  return out;
}

/** Letter fields a rule can always use (spec §3); `$count` etc. are live template fields, not condition fields. */
export const LETTER_FIELDS = [
  "$schema",
  "$sourceId",
  "$sourceType",
  "$service",
  "$instance",
  "$environment",
  "$severity",
  "$status",
  "$summary",
  "$correlationKey",
  "$eventTime",
  "$eventId",
  "$receivedAt",
  "$severityRaw",
];

export const LIVE_FIELDS = ["$count", "$state", "$alertUrl"];

/** Text for a JSON value in one line. */
export function showValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
