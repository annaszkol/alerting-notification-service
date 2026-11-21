import { fieldsFor, guessSeverity } from "./ConnectSource";

describe("connect source wizard", () => {
  it("guesses what a raw severity value means", () => {
    expect(["FATAL", "ERROR", "warn", "INFO", "debug"].map(guessSeverity)).toEqual(["critical", "error", "warning", "info", "info"]);
  });

  it("turns the picked fields into a mapping", () => {
    expect(fieldsFor({ service: "app", summary: "msg", severity: "level", problem: "order.id" }, [["ERR", "error"], ["", "info"]], null)).toEqual({
      $service: "app",
      $summary: "{{msg}}",
      $severity: { from: "level", map: { ERR: "error" } },
      $correlationKey: "{{order.id}}",
    });
    expect(fieldsFor({ service: "app", summary: "msg" }, [], "warning")).toEqual({ $service: "app", $summary: "{{msg}}", $severity: { const: "warning" } });
  });
});
