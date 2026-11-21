import { fireEvent, screen } from "@testing-library/react";

import { renderWith } from "../test-utils";
import { ConditionBuilder } from "./ConditionBuilder";

it("adds a condition and reports the condition JSON", () => {
  const onChange = vi.fn();
  renderWith(<ConditionBuilder value={{ all: [{ $service: "payments-api" }] }} onChange={onChange} paths={["$severity"]} />);
  expect(screen.getByDisplayValue("$service")).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled(); // loading never rewrites the document

  fireEvent.click(screen.getByRole("button", { name: "+ Condition" }));
  const fields = screen.getAllByLabelText("Field");
  fireEvent.change(fields[1], { target: { value: "$severity" } });
  const values = screen.getAllByLabelText("Value");
  fireEvent.change(values[1], { target: { value: "critical" } });

  expect(onChange).toHaveBeenLastCalledWith({ all: [{ $service: "payments-api" }, { $severity: "critical" }] });
});

it("shows nothing editable in read-only mode", () => {
  renderWith(<ConditionBuilder value={{ all: [{ a: 1 }] }} onChange={() => {}} paths={[]} readOnly />);
  expect(screen.queryByRole("button", { name: "+ Condition" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove condition" })).not.toBeInTheDocument();
});
