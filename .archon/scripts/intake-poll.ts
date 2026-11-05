// Hands new GitHub issue activity to Archon's intake without a webhook.
//
// Archon starts intake from forge events, which need a public webhook URL a laptop
// doesn't have. Archon's documented alternative for that case is a poll script that
// reports each item with `archon trigger fire --delivery-id`, so Archon dedupes and
// queues the starts itself. This is that script: run it on a timer (factory-schedule
// installs one). It decides nothing; intake does all the judging.
//
// Usage: bun .archon/scripts/intake-poll.ts [project-root] [--dry-run]

import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { readFactoryConfig } from "./factory-config.ts";

/** Labels intake reacts to (SDLC pack README, "Intake"); any other label is ignored there too. */
export const INTAKE_LABELS = ["archon-triage", "archon-ready", "archon-plan", "archon-investigate"];

export interface IssueSummary {
  number: number;
  title: string;
  body: string | null;
  state: string;
  created_at: string;
  pull_request?: unknown;
}
export interface IssueEvent {
  id: number;
  event: string;
  created_at: string;
  actor?: { login?: string } | null;
  label?: { name?: string } | null;
  issue?: { number?: number; state?: string; pull_request?: unknown } | null;
}
export interface Delivery { deliveryId: string; inputs: Record<string, string | number> }

/**
 * One delivery per open issue per distinct title+body, and one per intake label added.
 * Keys are stable, so re-reporting the same item is a no-op in Archon; a comment or a
 * label change on an issue doesn't change its key, an edit to the text does.
 */
export function deliveriesFor(repoHost: string, repoPath: string, issues: IssueSummary[], events: IssueEvent[], since: string): Delivery[] {
  const out: Delivery[] = [];
  const base = { repo_host: repoHost, repo_path: repoPath, kind: "issue" };
  for (const issue of issues) {
    if (issue.pull_request || issue.state !== "open") continue;
    const digest = createHash("sha256").update(`${issue.title}\n${issue.body ?? ""}`).digest("hex").slice(0, 16);
    out.push({ deliveryId: `issue:${issue.number}:${digest}`, inputs: { ...base, number: issue.number, action: "opened" } });
  }
  for (const event of events) {
    const number = event.issue?.number, label = event.label?.name ?? "", actor = event.actor?.login ?? "";
    if (event.event !== "labeled" || event.created_at < since || !number || event.issue?.pull_request) continue;
    if (event.issue?.state !== "open" || !INTAKE_LABELS.includes(label) || !actor) continue;
    out.push({ deliveryId: `label:${event.id}`, inputs: { ...base, number, action: "added", label, label_actor: actor } });
  }
  return out;
}

function gh(args: string[]): unknown {
  const result = Bun.spawnSync([process.env.GH_BIN || "gh", ...args], { stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0) throw new Error(`gh ${args.join(" ")} failed: ${result.stderr.toString().trim()}`);
  return JSON.parse(result.stdout.toString() || "null");
}

function main(): void {
  const args = process.argv.slice(2);
  const dryRun = args.includes("--dry-run");
  const root = resolve(args.find(arg => !arg.startsWith("--")) ?? ".");
  const config = readFactoryConfig(join(root, ".archon", "factory.yaml"));
  const repo = config.project.repo_path;
  const binding = join(root, ".archon", "triggers", "intake.json");
  if (!/^[^/\s]+\/[^/\s]+$/.test(repo)) throw new Error("project.repo_path in .archon/factory.yaml must be OWNER/REPO");
  if (!existsSync(binding)) throw new Error(`${binding} is missing: enable triggers in .archon/factory.yaml and run factory-setup`);

  // Look back three intervals (at least 15 min) so a missed or slow tick loses nothing.
  const lookback = Math.max(config.triggers.intake.interval_seconds * 3, 900);
  const since = new Date(Date.now() - lookback * 1000).toISOString().replace(/\.\d+Z$/, "Z");
  const issues = gh(["api", `repos/${repo}/issues?state=open&since=${since}&per_page=100`]) as IssueSummary[];
  const events = gh(["api", `repos/${repo}/issues/events?per_page=100`]) as IssueEvent[];
  const deliveries = deliveriesFor(config.project.repo_host, repo, issues, events, since);

  const archon = process.env.ARCHON_BIN || "archon";
  const scratch = mkdtempSync(join(tmpdir(), "factory-intake-"));
  const fired: string[] = [], failed: { deliveryId: string; error: string }[] = [];
  try {
    for (const delivery of deliveries) {
      if (dryRun) { fired.push(delivery.deliveryId); continue; }
      const inputs = join(scratch, "inputs.json");
      writeFileSync(inputs, JSON.stringify(delivery.inputs));
      const result = Bun.spawnSync([archon, "trigger", "fire", "--config", binding, "--delivery-id", delivery.deliveryId, "--inputs", inputs], { cwd: root, stdout: "pipe", stderr: "pipe" });
      if (result.exitCode === 0) fired.push(delivery.deliveryId);
      else failed.push({ deliveryId: delivery.deliveryId, error: (result.stderr.toString() || result.stdout.toString()).trim().slice(0, 400) });
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
  console.log(JSON.stringify({ at: new Date().toISOString(), repo, since, reported: fired.length, failed, dry_run: dryRun, deliveries: fired }));
  if (failed.length) process.exitCode = 1;
}

if (import.meta.main) main();
