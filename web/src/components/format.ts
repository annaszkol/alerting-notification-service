// Small validators shared by the forms; the patterns are the ones in contracts/openapi.yaml.

export const SLUG = /^[a-z0-9][a-z0-9_.-]*$/;
export const DURATION = /^[0-9]+(s|m|h|d)$/;
export const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
export const BETWEEN = /^([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]$/;
export const E164 = /^\+[1-9][0-9]{6,14}$/;

/** "Settlements prod failures" -> "settlements-prod-failures" (a suggestion for the id field). */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^[^a-z0-9]+/, "")
    .replace(/-+$/, "")
    .slice(0, 100);
}

export function slugError(value: string): string | null {
  if (!value) return "Required";
  return SLUG.test(value) ? null : "Lowercase letters, digits, '-', '_' or '.'; must start with a letter or digit";
}

export const TIMEZONES: string[] = (() => {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone");
    if (list?.length) return list.includes("UTC") ? list : ["UTC", ...list];
  } catch {
    // older browsers: fall back to a short list
  }
  return ["UTC", "Europe/Warsaw", "Europe/London", "America/New_York", "Asia/Singapore"];
})();

export const WEEKDAYS = [
  { value: "1", label: "Mon" },
  { value: "2", label: "Tue" },
  { value: "3", label: "Wed" },
  { value: "4", label: "Thu" },
  { value: "5", label: "Fri" },
  { value: "6", label: "Sat" },
  { value: "7", label: "Sun" },
];
