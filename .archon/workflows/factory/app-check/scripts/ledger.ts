import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

/**
 * What the rolling sweep last saw per scenario on the default branch. Ids, verdicts and issue
 * numbers only, never scenario text. Lives beside the scenarios, outside the repository.
 */
export interface LedgerEntry { verdict: "PASS" | "FAIL" | "INCOMPLETE"; commit: string; at: string; issue: number | null }
export interface Ledger { scenarios: Record<string, LedgerEntry> }

export const holdoutDir = (path: string) => resolve(path.replace(/^~(?=[/\\]|$)/, homedir()));
const ledgerPath = (path: string) => join(holdoutDir(path), ".ledger.json");

export function readLedger(path: string): Ledger {
  const file = ledgerPath(path);
  if (!existsSync(file)) return { scenarios: {} };
  const text = readFileSync(file, "utf8");
  return JSON.parse(text.startsWith("﻿") ? text.slice(1) : text) as Ledger;
}

export function writeLedger(path: string, ledger: Ledger): void {
  writeFileSync(ledgerPath(path), `${JSON.stringify(ledger, null, 2)}\n`);
}

/** Scenarios failing on the default branch with an open issue: id to issue number. */
export function knownFailures(ledger: Ledger): Record<string, number> {
  const known: Record<string, number> = {};
  for (const [id, entry] of Object.entries(ledger.scenarios)) if (entry.issue !== null) known[id] = entry.issue;
  return known;
}
