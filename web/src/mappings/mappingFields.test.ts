import { describe, expect, it } from "vitest";

import { formatPath, leafPaths } from "../components/paths";
import { applyPick, parseField, serializeField, setField } from "./mappingFields";

describe("mapping fields", () => {
  it("round-trips the spec §3 mapping values", () => {
    const values = [
      "labels.service",
      "{{workflowName}} / {{step}}: {{error.message}}",
      { const: "workflow" },
      { from: "severity", map: { INFO: "info", WARN: "warning" } },
      { from: "timestamp", format: "iso8601" },
    ];
    for (const v of values) expect(serializeField(parseField(v))).toEqual(v);
  });

  it("keeps a value it does not understand", () => {
    expect(serializeField(parseField({ odd: [1] }))).toEqual({ odd: [1] });
  });

  it("removes a field that is left empty", () => {
    expect(setField({ $service: "a", $summary: "x" }, "$service", parseField(""))).toEqual({ $summary: "x" });
  });

  it("a click sets the path or appends to a template", () => {
    expect(serializeField(applyPick(parseField("old"), 'labels["app.kubernetes.io/name"]'))).toBe('labels["app.kubernetes.io/name"]');
    expect(serializeField(applyPick(parseField("{{a}} "), "b.c"))).toBe("{{a}} {{b.c}}");
    expect(serializeField(applyPick(parseField({ from: "x", map: { A: "info" } }), "level"))).toEqual({ from: "level", map: { A: "info" } });
  });
});

describe("paths", () => {
  it("uses brackets for keys with a dot (spec §3)", () => {
    expect(formatPath(["labels", "app.kubernetes.io/name"])).toBe('labels["app.kubernetes.io/name"]');
    expect(formatPath(["alerts", 0, "labels", "service"])).toBe("alerts.0.labels.service");
    expect(formatPath(["a b", 'q"x'])).toBe('a b["q\\"x"]');
    expect(formatPath(["12"])).toBe('["12"]');
  });

  it("lists leaf paths of a sample", () => {
    expect(leafPaths({ a: { b: 1 }, "x.y": "z", l: [true] }).map((p) => p.path)).toEqual(["a.b", '["x.y"]', "l.0"]);
  });
});
