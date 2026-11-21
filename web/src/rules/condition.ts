// Condition JSON <-> an editable tree (spec §4, contracts/condition.md, libs/conditions/evaluate.py).
//
// Accepted input forms (both are stored in the wild):
//   {"all": [...]}, {"any": [...]}, {"not": c}
//   {"<path>": value}                     equality
//   {"<path>": {"<op>": arg, ...}}        every operator must hold
//   several keys in one object            all must hold
//   {"field": p, "op": o, "value": v}     contracts/condition.py form
// Output is always the spec form. The editor only serializes after a change, so an untouched rule is saved
// byte for byte as it was loaded.

import type { Condition } from "../api/types";

export const OPERATORS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "glob", "regex", "contains", "exists"] as const;
export type Operator = (typeof OPERATORS)[number];

export const OPERATOR_LABEL: Record<Operator, string> = {
  eq: "equals",
  ne: "does not equal",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  in: "is one of",
  not_in: "is none of",
  glob: "matches glob (* ?)",
  regex: "matches regex",
  contains: "contains",
  exists: "exists",
};

export type CondNode =
  | { id: number; kind: "group"; op: "all" | "any"; children: CondNode[] }
  | { id: number; kind: "not"; child: CondNode }
  | { id: number; kind: "leaf"; path: string; op: Operator; text: string }
  | { id: number; kind: "raw"; json: unknown };

let nextId = 1;
const newId = () => nextId++;

const COMBINATORS = new Set(["all", "any", "not"]);
const OPSET = new Set<string>(OPERATORS);

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isOpsObject(v: unknown): v is Record<string, unknown> {
  return isPlainObject(v) && Object.keys(v).length > 0 && Object.keys(v).every((k) => OPSET.has(k));
}

export function leaf(path = "", op: Operator = "eq", text = ""): CondNode {
  return { id: newId(), kind: "leaf", path, op, text };
}

export function group(op: "all" | "any" = "all", children: CondNode[] = []): CondNode {
  return { id: newId(), kind: "group", op, children };
}

export function negate(child: CondNode): CondNode {
  return { id: newId(), kind: "not", child };
}

export function parseCondition(cond: unknown): CondNode {
  if (!isPlainObject(cond) || Object.keys(cond).length === 0) return { id: newId(), kind: "raw", json: cond };
  const keys = Object.keys(cond);

  if (keys.includes("field") && keys.includes("op") && keys.every((k) => ["field", "op", "value"].includes(k))) {
    const op = String(cond.op);
    if (typeof cond.field === "string" && OPSET.has(op)) {
      return { id: newId(), kind: "leaf", path: cond.field, op: op as Operator, text: toText(cond.value, op as Operator) };
    }
    return { id: newId(), kind: "raw", json: cond };
  }

  if (keys.length > 1) return group("all", keys.map((k) => parseCondition({ [k]: cond[k] })));

  const key = keys[0];
  const value = cond[key];
  if (key === "all" || key === "any") {
    return Array.isArray(value) ? group(key, value.map(parseCondition)) : { id: newId(), kind: "raw", json: cond };
  }
  if (key === "not") return { id: newId(), kind: "not", child: parseCondition(value) };
  if (isOpsObject(value)) {
    const ops = Object.entries(value);
    const leaves = ops.map(([op, arg]) => ({ id: newId(), kind: "leaf" as const, path: key, op: op as Operator, text: toText(arg, op as Operator) }));
    return leaves.length === 1 ? leaves[0] : group("all", leaves);
  }
  return { id: newId(), kind: "leaf", path: key, op: "eq", text: toText(value, "eq") };
}

export function serializeCondition(node: CondNode): Condition {
  switch (node.kind) {
    case "group":
      return { [node.op]: node.children.map(serializeCondition) };
    case "not":
      return { not: serializeCondition(node.child) };
    case "raw":
      return node.json as Condition;
    case "leaf": {
      const value = fromText(node.text, node.op);
      const path = node.path.trim();
      if (COMBINATORS.has(path)) return { field: path, op: node.op, value }; // a payload key named all/any/not
      if (node.op === "eq" && !isOpsObject(value)) return { [path]: value };
      return { [path]: { [node.op]: value } };
    }
  }
}

// --- values ---------------------------------------------------------------------------------------------
// Inputs are text. A plain number / true / false / null is read as that JSON value; wrap text in double quotes to
// keep it a string ("123"). Lists (in, not_in) are comma separated. glob / regex / contains are always text.

function scalarFromText(raw: string): unknown {
  const text = raw.trim();
  if (text === "") return "";
  if (text.startsWith('"') || text.startsWith("{") || text.startsWith("[")) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  if (text === "true") return true;
  if (text === "false") return false;
  if (text === "null") return null;
  if (/^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?$/.test(text)) return Number(text);
  return text;
}

function scalarToText(value: unknown): string {
  if (typeof value === "string") {
    return scalarFromText(value) === value ? value : JSON.stringify(value);
  }
  return value === undefined ? "" : JSON.stringify(value);
}

export function splitList(text: string): string[] {
  const out: string[] = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"' && text[i - 1] !== "\\") quoted = !quoted;
    if (ch === "," && !quoted) {
      out.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  out.push(current);
  return out.map((s) => s.trim()).filter((s) => s !== "");
}

export function fromText(text: string, op: Operator): unknown {
  switch (op) {
    case "exists":
      return text !== "false";
    case "in":
    case "not_in":
      return splitList(text).map(scalarFromText);
    case "glob":
    case "regex":
    case "contains":
      return text;
    default:
      return scalarFromText(text);
  }
}

export function toText(value: unknown, op: Operator): string {
  switch (op) {
    case "exists":
      return value === false ? "false" : "true";
    case "in":
    case "not_in":
      return Array.isArray(value)
        ? value.map((v) => (typeof v === "string" && v.includes(",") ? JSON.stringify(v) : scalarToText(v))).join(", ")
        : scalarToText(value);
    case "glob":
    case "regex":
    case "contains":
      return typeof value === "string" ? value : scalarToText(value);
    default:
      return scalarToText(value);
  }
}

/** What the value will be stored as, shown next to the input so "10000000" vs "\"10000000\"" is visible. */
export function valueKind(text: string, op: Operator): string {
  const value = fromText(text, op);
  if (Array.isArray(value)) return `list of ${value.length}`;
  if (value === null) return "null";
  return typeof value === "string" ? "text" : typeof value;
}

// --- checks ---------------------------------------------------------------------------------------------

export interface ConditionProblem {
  nodeId: number;
  message: string;
}

/** Problems the UI blocks saving on: an empty group (an empty `all` matches every event), a leaf without a path. */
export function conditionProblems(node: CondNode, out: ConditionProblem[] = []): ConditionProblem[] {
  if (node.kind === "group") {
    if (node.children.length === 0) {
      out.push({ nodeId: node.id, message: node.op === "all" ? "Empty group: would match every event" : "Empty group: never matches" });
    }
    node.children.forEach((c) => conditionProblems(c, out));
  } else if (node.kind === "not") {
    conditionProblems(node.child, out);
  } else if (node.kind === "leaf") {
    if (!node.path.trim()) out.push({ nodeId: node.id, message: "Choose a field" });
    // regex syntax is Python's (re.search) and is not checked here; the tester shows whether it matches
    if ((node.op === "in" || node.op === "not_in") && splitList(node.text).length === 0) {
      out.push({ nodeId: node.id, message: "Give at least one value" });
    }
  }
  return out;
}

/** Immutable update of the node with `id`. `replace` returns the new node, or null to delete it. */
export function updateNode(root: CondNode, id: number, replace: (n: CondNode) => CondNode | null): CondNode | null {
  if (root.id === id) return replace(root);
  if (root.kind === "group") {
    const children: CondNode[] = [];
    for (const c of root.children) {
      const next = updateNode(c, id, replace);
      if (next) children.push(next);
    }
    return { ...root, children };
  }
  if (root.kind === "not") {
    const child = updateNode(root.child, id, replace);
    return child ? { ...root, child } : group("all");
  }
  return root;
}
