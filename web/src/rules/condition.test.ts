import { describe, expect, it } from "vitest";

import { conditionProblems, fromText, group, leaf, parseCondition, serializeCondition, toText, valueKind } from "./condition";

const roundTrip = (cond: unknown) => serializeCondition(parseCondition(cond));

describe("condition JSON <-> tree", () => {
  it("keeps the spec §4 rules identical", () => {
    const rules = [
      { all: [{ type: "WORKFLOW_FAILED" }, { workflowName: "order-settlement" }, { $environment: "prod" }] },
      { all: [{ $service: { glob: "pricing-engine-*" } }, { $severity: { gte: "warning" } }] },
      { all: [{ $schema: { glob: "trade.rejected.*" } }, { "payload.desk": "rates" }, { "payload.notional": { gt: 10000000 } }] },
      { all: [{ $service: "payments-api" }, { $severity: "critical" }] },
      { any: [{ "error.code": { in: ["TIMEOUT", "REJECTED"] } }, { not: { 'labels["app.kubernetes.io/name"]': { exists: true } } }] },
    ];
    for (const rule of rules) expect(roundTrip(rule)).toEqual(rule);
  });

  it("builds a nested any inside all as the contract expects", () => {
    const tree = group("all", [leaf("$sourceId", "eq", "pricing-logs"), group("any", [leaf("$severity", "gte", "error"), leaf("msg", "contains", "timeout")])]);
    expect(serializeCondition(tree)).toEqual({
      all: [{ $sourceId: "pricing-logs" }, { any: [{ $severity: { gte: "error" } }, { msg: { contains: "timeout" } }] }],
    });
  });

  it("reads the contracts/condition.py field/op/value form", () => {
    expect(roundTrip({ field: "payload.notional", op: "gt", value: 10 })).toEqual({ "payload.notional": { gt: 10 } });
  });

  it("splits several keys and several operators into an all group with the same meaning", () => {
    expect(roundTrip({ a: 1, b: { gte: 2, lt: 5 } })).toEqual({ all: [{ a: 1 }, { all: [{ b: { gte: 2 } }, { b: { lt: 5 } }] }] });
  });

  it("keeps a condition it does not understand as raw JSON", () => {
    expect(roundTrip({ all: "nope" })).toEqual({ all: "nope" });
  });

  it("writes equality with an operator-like object value explicitly", () => {
    expect(serializeCondition(leaf("x", "eq", '{"gt": 1}'))).toEqual({ x: { eq: { gt: 1 } } });
  });
});

describe("values", () => {
  it("reads numbers, booleans and quoted strings", () => {
    expect(fromText("25000000", "gt")).toBe(25000000);
    expect(fromText("true", "eq")).toBe(true);
    expect(fromText('"123"', "eq")).toBe("123");
    expect(fromText("rates", "eq")).toBe("rates");
    expect(fromText("TIMEOUT, REJECTED", "in")).toEqual(["TIMEOUT", "REJECTED"]);
    expect(fromText('"a,b", c', "in")).toEqual(["a,b", "c"]);
    expect(fromText("123", "contains")).toBe("123");
    expect(fromText("false", "exists")).toBe(false);
  });

  it("shows strings that look like numbers in quotes so they stay strings", () => {
    expect(toText("123", "eq")).toBe('"123"');
    expect(toText(123, "eq")).toBe("123");
    expect(toText(["a,b", "c"], "in")).toBe('"a,b", c');
    expect(valueKind("12", "eq")).toBe("number");
    expect(valueKind('"12"', "eq")).toBe("text");
  });
});

describe("problems", () => {
  it("flags empty groups and leaves without a field", () => {
    expect(conditionProblems(group("all")).map((p) => p.message)).toEqual(["Empty group: would match every event"]);
    expect(conditionProblems(group("all", [leaf("", "eq", "x")])).map((p) => p.message)).toEqual(["Choose a field"]);
    expect(conditionProblems(group("all", [leaf("a", "eq", "x")]))).toEqual([]);
  });
});

describe("sentences", () => {
  it("describes a condition in words", async () => {
    const { describeCondition } = await import("./condition");
    expect(describeCondition({ all: [{ $service: "payments-api" }, { $severity: { gte: "critical" } }] })).toBe("Service is payments-api and Severity is at least critical");
    expect(describeCondition({ any: [{ "labels.env": "prod" }, { not: { $status: "resolved" } }] })).toBe("labels.env is prod or not Status is resolved");
    expect(describeCondition({ all: [] })).toBe("any event");
  });

  it("maps plain field names to letter fields and back", async () => {
    const { fieldLabel, fieldPath } = await import("./condition");
    expect([fieldLabel("$summary"), fieldPath("Message"), fieldPath("error.code")]).toEqual(["Message", "$summary", "error.code"]);
  });
});
