import { fireEvent, screen } from "@testing-library/react";

import { renderWith } from "../test-utils";
import { ConditionBuilder } from "./ConditionBuilder";

it("adds a condition and reports the condition JSON", () => {
  const onChange = vi.fn();
  renderWith(<ConditionBuilder value={{ all: [{ $service: "payments-api" }] }} onChange={onChange} paths={["$severity"]} />);
  expect(screen.getByDisplayValue("Service")).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled(); // loading never rewrites the document

  fireEvent.click(screen.getByRole("button", { name: "+ Condition" }));
  const fields = screen.getAllByLabelText("Field");
  fireEvent.change(fields[1], { target: { value: "Message" } });
  const values = screen.getAllByLabelText("Value");
  fireEvent.change(values[1], { target: { value: "disk full" } });

  expect(onChange).toHaveBeenLastCalledWith({ all: [{ $service: "payments-api" }, { $summary: "disk full" }] });
});

it("severity is picked from a list, not typed", async () => {
  const onChange = vi.fn();
  renderWith(<ConditionBuilder value={{ all: [{ $severity: { gte: "warning" } }] }} onChange={onChange} paths={[]} />);
  expect(screen.getByDisplayValue("Severity")).toBeInTheDocument();
  expect(screen.getByDisplayValue("is at least")).toBeInTheDocument();
  fireEvent.click(screen.getAllByLabelText("Value")[0]);
  fireEvent.click(await screen.findByRole("option", { name: "critical" }));
  expect(onChange).toHaveBeenLastCalledWith({ all: [{ $severity: { gte: "critical" } }] });
});

it("shows nothing editable in read-only mode", () => {
  renderWith(<ConditionBuilder value={{ all: [{ a: 1 }] }} onChange={() => {}} paths={[]} readOnly />);
  expect(screen.queryByRole("button", { name: "+ Condition" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove condition" })).not.toBeInTheDocument();
});
