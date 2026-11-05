// Answers one question: can this factory take an issue all the way to a merge?
//
// factory-setup only applies settings. A repo can pass setup and still never merge:
// no maintainers (nothing is trusted), no CI (the merge queue needs green checks), or
// no way for new issues to reach intake. This reads the facts and says what's missing,
// with the fix for each. It changes nothing.
//
// Usage: bun .archon/scripts/factory-ready.ts [project-root]

import { existsSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { readFactoryConfig } from "./factory-config.ts";

export interface Check { name: string; ok: boolean; detail: string; fix: string }

const sh = (command: string[], cwd: string) => {
  const result = Bun.spawnSync(command, { cwd, stdout: "pipe", stderr: "pipe" });
  return { ok: result.exitCode === 0, out: result.stdout.toString().trim(), err: result.stderr.toString().trim() };
};
const gh = (args: string[], cwd: string) => sh([process.env.GH_BIN || "gh", ...args], cwd);
const archonBin = () => process.env.ARCHON_BIN || "archon";

/** The default branch's latest commit checks, as the merge queue reads them. */
export function checkState(runs: { status: string; conclusion: string | null }[], statuses: { state: string }[]): "none" | "pending" | "green" | "red" {
  const passed = new Set(["success", "neutral", "skipped"]);
  if (!runs.length && !statuses.length) return "none";
  if (runs.some(run => run.status === "completed" && !passed.has(String(run.conclusion))) || statuses.some(s => s.state === "failure" || s.state === "error")) return "red";
  if (runs.some(run => run.status !== "completed") || statuses.some(s => s.state === "pending")) return "pending";
  return "green";
}

export function readiness(root: string): { ready: boolean; checks: Check[] } {
  const checks: Check[] = [];
  const add = (name: string, ok: boolean, detail: string, fix = "") => checks.push({ name, ok, detail, fix: ok ? "" : fix });

  let config;
  try { config = readFactoryConfig(join(root, ".archon", "factory.yaml")); }
  catch (error) {
    add("factory.yaml", false, String(error instanceof Error ? error.message : error), "Fix .archon/factory.yaml, then run factory-setup.");
    return { ready: false, checks };
  }
  const repo = config.project.repo_path;
  const repoOk = /^[^/\s]+\/[^/\s]+$/.test(repo);
  add("repo", repoOk, repoOk ? repo : "project.repo_path is not set", "Set project.repo_path: OWNER/REPO in .archon/factory.yaml.");
  if (!repoOk) return { ready: false, checks };

  const view = gh(["repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"], root);
  add("GitHub access", view.ok, view.ok ? `default branch ${view.out}` : view.err.slice(0, 200), "Run gh auth login, and check the repo exists.");
  if (!view.ok) return { ready: false, checks };
  const branch = view.out;

  const local = sh(["git", "rev-parse", "HEAD"], root).out, remote = sh(["git", "ls-remote", "origin", `refs/heads/${branch}`], root).out.split(/\s/)[0] ?? "";
  add("pushed", !!local && local === remote, local === remote ? `${branch} is pushed` : `local ${local.slice(0, 8) || "?"} vs origin/${branch} ${remote.slice(0, 8) || "missing"}`,
    `Commit and push ${branch}: the queues read policy and CI from the default branch on GitHub, not your checkout.`);

  const file = gh(["api", `repos/${repo}/contents/.archon/sdlc/policy/maintainers.json?ref=${branch}`, "--jq", ".content"], root);
  let maintainers: string[] = [];
  if (file.ok) {
    try {
      const parsed = JSON.parse(Buffer.from(file.out.replace(/\s/g, ""), "base64").toString("utf8"));
      maintainers = (Array.isArray(parsed) ? parsed : []).map((e: unknown) => (typeof e === "string" ? e : String((e as { login?: string })?.login ?? ""))).filter(Boolean);
    } catch { /* reported below */ }
  }
  const me = gh(["api", "user", "--jq", ".login"], root).out;
  const trusted = maintainers.map(login => login.toLowerCase()).includes(me.toLowerCase());
  add("maintainers", maintainers.length > 0 && (!me || trusted),
    maintainers.length ? `${maintainers.join(", ")} on ${branch}${me && !trusted ? ` (you, ${me}, are not listed)` : ""}` : `no .archon/sdlc/policy/maintainers.json on ${branch}`,
    `Add your login to maintainers in .archon/factory.yaml (or let setup add ${me || "yours"}), run factory-setup, commit and push. Without it no issue is trusted and nothing merges.`);

  const workflows = gh(["api", `repos/${repo}/contents/.github/workflows?ref=${branch}`, "--jq", "[.[].name] | join(\",\")"], root);
  add("CI workflow", workflows.ok && !!workflows.out, workflows.ok && workflows.out ? workflows.out : `no .github/workflows on ${branch}`,
    "Keep ci.enabled: true and run factory-setup (it writes .github/workflows/factory-ci.yml), then commit and push.");

  const runs = gh(["api", `repos/${repo}/commits/${branch}/check-runs`, "--jq", "[.check_runs[] | {status, conclusion}]"], root);
  const statuses = gh(["api", `repos/${repo}/commits/${branch}/status`, "--jq", "[.statuses[] | {state}]"], root);
  const state = runs.ok && statuses.ok ? checkState(JSON.parse(runs.out || "[]"), JSON.parse(statuses.out || "[]")) : "none";
  add("CI green on main", state === "green", `checks on the latest ${branch} commit: ${state}`,
    state === "pending" ? "Wait for the running checks to finish, then check again." : state === "red" ? `Fix the failing check on ${branch}: the merge queue merges nothing while ${branch} is red.` : "Push a commit so CI runs at least once on the default branch.");

  const skill = [".claude", ".agents"].some(dir => existsSync(join(root, dir, "skills", "archon-cli", "SKILL.md")));
  add("archon-cli skill", skill, skill ? "installed" : "missing", "Run archon skill add . from the project root.");

  const doctor = sh([archonBin(), "doctor"], root);
  const provider = `${doctor.out}\n${doctor.err}`.match(/Assistant login:\s*([\w-]+):\s*usable/i)?.[1] ?? "";
  add("provider", !!provider, provider ? `${provider} is usable` : "no usable default provider", "Set defaultAssistant in ~/.archon/config.yaml (or run archon setup) and log in to that provider.");

  const bindings = existsSync(join(root, ".archon", "triggers")) ? readdirSync(join(root, ".archon", "triggers")).filter(name => name.endsWith(".json")) : [];
  const intake = config.triggers.enabled && bindings.includes("intake.json");
  add("intake", intake, intake ? `poll every ${config.triggers.intake.interval_seconds}s` : "new issues have no way to reach intake",
    "Set triggers.enabled: true (with host_id and run_as_user_id from archon trigger whoami), run factory-setup, then factory-schedule install.");

  const status = sh([process.execPath, join(root, ".archon", "scripts", "factory-schedule.ts"), "status", root], root);
  let timers: { name: string; installed: boolean }[] = [];
  try { const parsed = JSON.parse(status.out).result; timers = Array.isArray(parsed) ? parsed : []; } catch { /* reported below */ }
  const installed = process.platform !== "win32" ? status.ok : bindings.every(file => timers.some(t => t.name === file.replace(/\.json$/, "") && t.installed));
  add("timers", intake && installed, intake && installed ? `${bindings.length} timer(s) installed` : "the factory only runs when you start queues by hand",
    "Run bun .archon/scripts/factory-schedule.ts install (keep the machine on and awake while the factory works).");

  return { ready: checks.every(check => check.ok), checks };
}

if (import.meta.main) {
  const root = resolve(process.argv[2] ?? ".");
  const result = readiness(root);
  console.log(JSON.stringify(result, null, 2));
  if (!result.ready) process.exitCode = 1;
}
