import { backoffMs } from "./live";

it("reconnects after 1 s, 2 s, 4 s ... capped at 30 s (ws-messages rule 3)", () => {
  expect([0, 1, 2, 3, 4, 5, 6].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
});
