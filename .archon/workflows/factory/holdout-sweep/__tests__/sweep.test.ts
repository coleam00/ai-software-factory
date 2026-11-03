import { describe, expect, test } from "bun:test";
import { pickBatch } from "../scripts/plan.ts";
import { issueBody, parseOutcomes, plan } from "../scripts/record.ts";
import { knownFailures, type Ledger } from "../../app-check/scripts/ledger.ts";

const entry = (commit: string, at: string, issue: number | null = null, verdict: "PASS" | "FAIL" = "PASS") => ({ verdict, commit, at, issue });

describe("rolling holdout sweep", () => {
  test("checks never-run scenarios first, then the longest ago, and skips what this commit already covered", () => {
    const ledger: Ledger = { scenarios: { "H-01": entry("old", "2026-11-02"), "H-02": entry("old", "2026-11-01"), "H-04": entry("head", "2026-11-03") } };
    expect(pickBatch(["H-01", "H-02", "H-03", "H-04"], ledger, "head", 2)).toEqual(["H-03", "H-02"]);
    expect(pickBatch(["H-04"], ledger, "head", 10)).toEqual([]);
  });
  test("a batch never exceeds its size", () => {
    expect(pickBatch(["H-01", "H-02", "H-03"], { scenarios: {} }, "head", 2)).toHaveLength(2);
  });
  test("reads ids, verdicts and sentences from sealed lines and nothing else", () => {
    expect(parseOutcomes(["H-07  FAIL  The total is wrong.  evidence: a.png", "not a line"])).toEqual([{ id: "H-07", verdict: "FAIL", sentence: "The total is wrong." }]);
  });
  test("files a new failure once, clears on a pass, and INCOMPLETE changes neither", () => {
    const ledger: Ledger = { scenarios: { "H-01": entry("o", "t", 41, "FAIL"), "H-02": entry("o", "t", 42, "FAIL"), "H-03": entry("o", "t", 43, "FAIL") } };
    const result = plan(ledger, [
      { id: "H-01", verdict: "PASS", sentence: "" },
      { id: "H-02", verdict: "FAIL", sentence: "" },
      { id: "H-03", verdict: "INCOMPLETE", sentence: "" },
      { id: "H-04", verdict: "FAIL", sentence: "new" },
      { id: "H-05", verdict: "INCOMPLETE", sentence: "" },
    ]);
    expect(result.toFile.map(o => o.id)).toEqual(["H-04"]);
    expect(result.toClear).toEqual([{ id: "H-01", issue: 41 }]);
  });
  test("a known failure is a scenario with an open issue", () => {
    expect(knownFailures({ scenarios: { "H-01": entry("o", "t", 41, "FAIL"), "H-02": entry("o", "t") } })).toEqual({ "H-01": 41 });
  });
  test("a filed issue passes triage's contract and carries the marker the next sweep finds", () => {
    const body = issueBody({ id: "H-07", verdict: "FAIL", sentence: "The total is wrong." }, "main", "abc", "<!-- factory-holdout:H-07 -->");
    for (const heading of ["## Problem", "## Why", "## Why now", "## Outcome", "## Invariants", "## Acceptance"]) expect(body).toContain(heading);
    expect(body).toContain("<!-- factory-holdout:H-07 -->");
    expect(body).toContain("The total is wrong.");
  });
});
