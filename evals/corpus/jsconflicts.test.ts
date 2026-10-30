import { expect, test } from "vitest";
import { double } from "./jsconflicts";

test("double", () => {
  expect(double(2)).toBe(5); // contradicts the JSDoc example double(2) === 4
});
