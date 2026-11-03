import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { LINE } from "../../app-check/scripts/evidence.ts";
import { readLedger, writeLedger, type Ledger } from "../../app-check/scripts/ledger.ts";
import { emit, env, ghJson, jsonEnv, run } from "../../.shared/utils.ts";

export interface Outcome { id: string; verdict: "PASS" | "FAIL" | "INCOMPLETE"; sentence: string }
export const parseOutcomes = (lines: string[]): Outcome[] => lines.flatMap(line => {
  const match = line.match(LINE);
  return match && match[1]!.startsWith("H-") ? [{ id: match[1]!, verdict: match[2] as Outcome["verdict"], sentence: match[3]! }] : [];
});

/** What this batch means: a fail with no issue needs one; a pass with an issue clears it; INCOMPLETE never changes either. */
export function plan(ledger: Ledger, outcomes: Outcome[]): { toFile: Outcome[]; toClear: { id: string; issue: number }[] } {
  const prior = (id: string) => ledger.scenarios[id]?.issue ?? null;
  return {
    toFile: outcomes.filter(o => o.verdict === "FAIL" && prior(o.id) === null),
    toClear: outcomes.flatMap(o => {
      const issue = prior(o.id);
      return o.verdict === "PASS" && issue !== null ? [{ id: o.id, issue }] : [];
    }),
  };
}

export function issueBody(o: Outcome, base: string, head: string, marker: string): string {
  return `${marker}
## Problem

A held-out check fails on \`${base}\` at \`${head}\`. What a user sees: ${o.sentence}

## Why

The default branch does something the product should not. This check is private, so this issue describes the behaviour and never the check.

## Why now

The rolling holdout sweep found it. Pull requests that exercise this behaviour report it as a known failure until it is fixed.

## Outcome

The behaviour described above no longer happens.

## Invariants

Fix the behaviour a user sees. Nothing else in the product changes.

## Acceptance

- [ ] The behaviour described above no longer happens in the running app.
- [ ] The next holdout sweep reports ${o.id} passing, which clears this issue's known-failure record.
`;
}

if (import.meta.main) {
  const config = jsonEnv<{ holdouts: { path: string } }>("config", { holdouts: { path: "" } });
  const repo = env("repo_path"), base = env("base"), head = env("head"), now = new Date().toISOString();
  const outcomes = parseOutcomes(jsonEnv<string[]>("lines", []));
  const ledger = readLedger(config.holdouts.path);
  const { toFile, toClear } = plan(ledger, outcomes);
  const filed = new Map<string, number>();
  for (const o of toFile) {
    const marker = `<!-- factory-holdout:${o.id} -->`;
    const open = ghJson(["issue", "list", "--repo", repo, "--state", "open", "--search", `"factory-holdout:${o.id}" in:body`, "--json", "number,body"]) as { number: number; body: string }[];
    const existing = open.find(row => row.body.includes(marker));
    if (existing) { filed.set(o.id, existing.number); continue; }
    const file = join(process.env.ARTIFACTS_DIR!, "app-check", `issue-${o.id}.md`);
    writeFileSync(file, issueBody(o, base, head, marker));
    const url = run(["gh", "issue", "create", "--repo", repo, "--title", `A held-out check fails on ${base}: ${o.id}`, "--body-file", file]);
    filed.set(o.id, Number(url.trim().split("/").pop()));
  }
  for (const { issue } of toClear) run(["gh", "issue", "comment", String(issue), "--repo", repo, "--body", `The holdout sweep passes this check at \`${head}\`.`]);
  for (const o of outcomes) {
    const cleared = toClear.some(c => c.id === o.id);
    ledger.scenarios[o.id] = { verdict: o.verdict, commit: head, at: now, issue: cleared ? null : filed.get(o.id) ?? ledger.scenarios[o.id]?.issue ?? null };
  }
  writeLedger(config.holdouts.path, ledger);
  emit({ recorded: outcomes.length, filed: [...filed.values()], cleared: toClear.map(c => c.issue) });
}
