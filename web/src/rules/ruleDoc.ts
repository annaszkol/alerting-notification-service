// Pure helpers for the rule editor: a new rule, the document sent to the API, the checks that block saving and
// one-line summaries for the rule list.
import type { Owner, RouteEntry, RuleDoc } from "../api/types";
import { DURATION, slugError } from "../components/format";
import { conditionProblems, parseCondition } from "./condition";
import { recipientKind, routeProblems } from "./RouteEditor";

export function newRule(owner: Owner): RuleDoc {
  return {
    id: "",
    owner,
    enabled: true,
    when: { all: [] },
    route: [],
    present: { title: "" },
    dedup: { mode: "until_resolved" },
    runbooks: [],
  };
}

/** The RuleInput for POST / PUT: no server-set fields, no empty strings or empty optional objects. */
export function toInput(doc: RuleDoc): RuleDoc {
  const { updatedBy: _by, updatedAt: _at, ...rest } = doc;
  void _by;
  void _at;
  const out: RuleDoc = { ...rest, id: rest.id.trim() };

  if (out.present) {
    const present = Object.fromEntries(
      Object.entries(out.present).filter(([, v]) => v !== "" && v !== undefined && !(Array.isArray(v) && v.length === 0)),
    );
    if (Object.keys(present).length) out.present = present;
    else delete out.present;
  }
  if (out.dedup) {
    const dedup = { ...out.dedup };
    if (!dedup.key?.length) delete dedup.key;
    if (dedup.mode !== "window") delete dedup.window;
    out.dedup = dedup;
  }
  if (out.autoResolveAfter === "") out.autoResolveAfter = null;
  return out;
}

export function ruleProblems(doc: RuleDoc): string[] {
  const out: string[] = [];
  const idProblem = slugError(doc.id.trim());
  if (idProblem) out.push(`Rule id: ${idProblem}`);
  for (const p of conditionProblems(parseCondition(doc.when))) out.push(`Condition: ${p.message}`);
  out.push(...routeProblems(doc.route ?? []));
  if (doc.dedup?.mode === "window" && !DURATION.test(doc.dedup.window ?? "")) out.push("Grouping window: give a duration like 5m");
  if (doc.autoResolveAfter && !DURATION.test(doc.autoResolveAfter)) out.push("Auto-resolve: give a duration like 30m");
  return out;
}

const CHANNEL_LABEL: Record<string, string> = { ui: "the app", email: "email", sms: "SMS", phone: "phone call" };

function words(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Who gets notified, as words: `on-call of settlements by phone call and SMS`. */
export function routeSummary(route: RouteEntry[] | undefined): string {
  if (!route?.length) return "nobody (the alert only shows in the app)";
  return route
    .map((entry) => {
      const to = entry.to as Record<string, string>;
      const kind = recipientKind(entry.to);
      const who = kind === "oncall" ? `on-call of ${to.oncall}` : kind === "user" ? to.user : `team ${to.team}${to.role ? ` (${to.role}s)` : ""}`;
      const ch = entry.channels;
      const how = Array.isArray(ch)
        ? `by ${words(ch.map((c) => CHANNEL_LABEL[c] ?? c))}`
        : ch && typeof ch === "object"
          ? "by channels depending on the time of day"
          : "by their own preferences";
      return `${who} ${how}`;
    })
    .join("; ");
}
