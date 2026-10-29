import { describe, expect, test } from "bun:test";
import { validateBacklog } from "../scripts/check.ts";

const body = (key: string) => `## Problem\nA real problem.\n## Why\nUser value.\n## Why now\nNeeded now.\n## Outcome\nA running outcome.\n## Invariants\n- Keep data.\n## Acceptance\n- [ ] Observable result\n\n<!-- factory-backlog: ${key} -->`;

describe("backlog check", () => {
  test("accepts a clean ordered slice", () => {
    expect(validateBacklog([
      { key: "first-run", title: "Run the core path", body: body("first-run"), depends_on: [], size: "s" },
      { key: "next-step", title: "Add the next step", body: body("next-step"), depends_on: ["first-run"], size: "m" },
    ])).toEqual([]);
  });
  test("rejects missing contract elements and forward dependencies", () => {
    const errors = validateBacklog([{ key: "bad", title: "Bad", body: "<!-- factory-backlog: bad -->", depends_on: ["later"], size: "xl" }]);
    expect(errors.some(error => error.includes("lacks Problem"))).toBe(true);
    expect(errors.some(error => error.includes("earlier tickets"))).toBe(true);
  });
});
