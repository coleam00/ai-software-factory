import { describe, expect, test } from "bun:test";
import { DEFAULTS } from "../../../../scripts/factory-config.ts";
import { matchingFiles } from "../../.shared/utils.ts";
import { candidateBinding, oldestUnchecked } from "../scripts/select.ts";
import { scopeDecision } from "../scripts/scope.ts";
import { protectedGrantAllows } from "../scripts/policy.ts";
import { evidenceProblems } from "../scripts/evidence.ts";
import { boundDescription, statusCoversBinding } from "../scripts/binding.ts";
import { configuredHoldoutIds } from "../scripts/holdouts.ts";
import { bindingIsCurrent } from "../scripts/publish.ts";
import { finalizePending } from "../scripts/finalize.ts";
import { appProcessRecordPath, inspectAppProcessIdentity, recordAppProcess, stopRecordedApp } from "../scripts/app-process.ts";
import { installFakeCommand, isolatedCommandEnv } from "../../.shared/test-command.ts";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function spawnTestChild(): Bun.Subprocess {
  return Bun.spawn([process.execPath, "-e", "setInterval(() => {}, 1000)"], { stdin: "ignore", stdout: "ignore", stderr: "ignore", windowsHide: true });
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function stopTestChild(child: Bun.Subprocess): Promise<void> {
  if (processIsAlive(child.pid)) child.kill();
  await child.exited;
}

describe("app-check deterministic decisions", () => {
  test("matches anchored frontend globs", () => {
    expect(matchingFiles(["src/a/site.css", "app/views.ts", "src/server.ts"], DEFAULTS.app_check.frontend_paths)).toEqual(["src/a/site.css", "app/views.ts"]);
  });
  test("uses size labels for auto depth and skips backend-only changes", () => {
    const enabled = { ...DEFAULTS, app_check: { ...DEFAULTS.app_check, enabled: true } };
    expect(scopeDecision(enabled, ["src/server.ts"], ["archon-size-l"])).toEqual({ run: false, depth: "deep", reason: "skipped: no front-end change" });
    expect(scopeDecision(enabled, ["public/app.css"], ["archon-size-s"]).depth).toBe("light");
  });
  test("selects a draft before delivery marks it ready and skips only the exact tested binding", () => {
    const row = (number: number, createdAt: string, isDraft = false, baseRefOid = "base-a") => ({ number, createdAt, isDraft, headRefOid: `h${number}`, baseRefName: "main", baseRefOid, files: [], body: "" });
    const checked = row(1, "2026-01-01");
    expect(oldestUnchecked([row(2, "2026-02-01"), checked, row(3, "2025-01-01", true)], new Set([candidateBinding(checked)]))?.number).toBe(3);
    expect(oldestUnchecked([{ ...checked, baseRefOid: "base-b" }], new Set([candidateBinding(checked)]))?.number).toBe(1);
  });
  test("binds statuses and publication freshness to the exact base and head", () => {
    const statuses = [{ context: "factory/app-check", state: "success", description: boundDescription("main", "base-a", "passed") }];
    expect(statusCoversBinding(statuses, "main", "base-a")).toBe(true);
    expect(statusCoversBinding(statuses, "release", "base-a")).toBe(false);
    expect(statusCoversBinding(statuses, "main", "base-b")).toBe(false);
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-a", baseRefName: "main" }, "base-a", "head-a", "main", "base-a")).toBe(true);
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-b", baseRefName: "main" }, "base-a", "head-a", "main", "base-a")).toBe(false);
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-a", baseRefName: "main" }, "base-b", "head-a", "main", "base-a")).toBe(false);
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-a", baseRefName: "release" }, "base-a", "head-a", "main", "base-a")).toBe(false);
    const now = Date.parse("2026-10-29T12:00:00Z");
    const pending = (created_at: string) => [{ context: "factory/app-check", state: "pending", description: boundDescription("main", "base-a", "running"), created_at }];
    expect(statusCoversBinding(pending("2026-10-29T11:00:00Z"), "main", "base-a", now)).toBe(true);
    expect(statusCoversBinding(pending("2026-10-29T09:00:00Z"), "main", "base-a", now)).toBe(false);
  });
  test("requires an exact operator grant and never treats forbidding issue prose as approval", () => {
    const paths = [".archon/factory.yaml", ".github/workflows/ci.yml"];
    expect(protectedGrantAllows(JSON.stringify({ head: "head-a", paths }), "head-a", paths)).toBe(true);
    expect(protectedGrantAllows(JSON.stringify({ head: "head-a", paths: [paths[0]] }), "head-a", paths)).toBe(false);
    expect(protectedGrantAllows(JSON.stringify({ head: "head-a", paths, note: "trust me" }), "head-a", paths)).toBe(false);
    expect(protectedGrantAllows("## Current scope (2026-10-29)\nDo NOT change `.archon/factory.yaml`.", "head-a", [paths[0]!])).toBe(false);
  });
  test("builds a private id-only holdout manifest from configured filenames", () => {
    expect(configuredHoldoutIds(["H-02-remove.md", "notes.txt", "H-01-add.md"])).toEqual(["H-01", "H-02"]);
    expect(() => configuredHoldoutIds(["H-bad.md", "H-bad-other.md"])).toThrow("unique");
  });
  test("seals evidence and rejects incomplete-as-pass", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-evidence-test-"));
    const preparedAt = Date.now();
    const evidence = join(root, "shot.txt");
    writeFileSync(evidence, "observed");
    utimesSync(evidence, new Date(preparedAt + 1000), new Date(preparedAt + 1000));
    const line = "C1  PASS  Add flow works.  evidence: shot.txt";
    expect(evidenceProblems({ overall: "PASS", lines: [line] }, line, root, preparedAt)).toEqual([]);
    const incomplete = "C1  INCOMPLETE  Browser stopped.  evidence: shot.txt";
    expect(evidenceProblems({ overall: "PASS", lines: [incomplete] }, incomplete, root, preparedAt)).toContain("overall must be FAIL");
    expect(evidenceProblems({ overall: "PASS", lines: [line] }, line, root, preparedAt, ["H-01"])).toContain("missing configured holdout result: H-01");
    const holdout = "H-01  PASS  Private invariant holds.  evidence: shot.txt";
    expect(evidenceProblems({ overall: "PASS", lines: [line, holdout] }, `${line}\n${holdout}`, root, preparedAt, ["H-01"])).toEqual([]);
  });
  test("evidence entrypoint accepts one leading BOM without relaxing JSON parsing", () => {
    const artifacts = mkdtempSync(join(tmpdir(), "factory-evidence-entrypoint-test-"));
    const root = join(artifacts, "app-check");
    mkdirSync(root, { recursive: true });
    const preparedAt = Date.now() - 10_000;
    const line = "C1  PASS  Add\uFEFFflow works.  evidence: shot.txt";
    writeFileSync(join(root, "shot.txt"), "observed");
    writeFileSync(join(root, "report.md"), line);
    const resultPath = join(root, "result.json");
    writeFileSync(resultPath, `\uFEFF${JSON.stringify({ overall: "PASS", lines: [line], summary: "observed" })}`);
    const script = join(import.meta.dir, "..", "scripts", "evidence.ts");
    const env = { ...process.env, ARTIFACTS_DIR: artifacts, INPUTS_PREPARED_AT: String(preparedAt), INPUTS_EXPECTED_HOLDOUTS: "[]" };
    const accepted = Bun.spawnSync([process.execPath, script], { env, stdout: "pipe", stderr: "pipe" });
    expect(accepted.exitCode, accepted.stderr.toString()).toBe(0);
    expect(JSON.parse(accepted.stdout.toString()).lines).toEqual([line]);

    writeFileSync(resultPath, `\uFEFF\uFEFF${JSON.stringify({ overall: "PASS", lines: [line], summary: "observed" })}`);
    const doubled = Bun.spawnSync([process.execPath, script], { env, stdout: "pipe", stderr: "pipe" });
    expect(doubled.exitCode).not.toBe(0);

    writeFileSync(resultPath, '\uFEFF{"overall":');
    const malformed = Bun.spawnSync([process.execPath, script], { env, stdout: "pipe", stderr: "pipe" });
    expect(malformed.exitCode).not.toBe(0);
  });
  test("turns an abandoned pending check into failure", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-finalize-test-"));
    mkdirSync(root, { recursive: true });
    writeFileSync(join(root, "pending.json"), JSON.stringify({ repo: "example/widgets", head: "head-a", base: "main", base_oid: "base-a" }));
    const calls: unknown[][] = [];
    expect(finalizePending(root, (...args) => { calls.push(args); })).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.slice(0, 4)).toEqual(["example/widgets", "head-a", "failure", "factory/app-check"]);
    expect(existsSync(join(root, "terminal.json"))).toBe(true);
    expect(finalizePending(root, (...args) => { calls.push(args); })).toBe(false);
    expect(calls).toHaveLength(1);
  });
  test("rejects missing, malformed, and legacy numeric-only ownership records", async () => {
    if (process.platform !== "win32") return;
    const artifacts = mkdtempSync(join(tmpdir(), "factory-app-process-invalid-test-"));
    const recordPath = appProcessRecordPath(artifacts);
    const child = spawnTestChild();
    try {
      const legacyPath = join(artifacts, "app-check", "app.pid");
      mkdirSync(join(artifacts, "app-check"), { recursive: true });
      writeFileSync(legacyPath, String(child.pid));
      expect(stopRecordedApp(recordPath)).toEqual({ status: "no-record" });
      expect(existsSync(legacyPath)).toBe(false);
      expect(processIsAlive(child.pid)).toBe(true);
      writeFileSync(recordPath, String(child.pid));
      expect(stopRecordedApp(recordPath).status).toBe("invalid-record");
      expect(processIsAlive(child.pid)).toBe(true);
      writeFileSync(recordPath, "{not-json");
      expect(stopRecordedApp(recordPath).status).toBe("invalid-record");
      expect(processIsAlive(child.pid)).toBe(true);
    } finally {
      rmSync(recordPath, { force: true });
      await stopTestChild(child);
    }
  });
  test("retires the identity record when the owned process already exited", async () => {
    if (process.platform !== "win32") return;
    const artifacts = mkdtempSync(join(tmpdir(), "factory-app-process-exited-test-"));
    const recordPath = appProcessRecordPath(artifacts);
    const child = spawnTestChild();
    recordAppProcess(child.pid, inspectAppProcessIdentity(child.pid), recordPath);
    await stopTestChild(child);
    expect(stopRecordedApp(recordPath)).toEqual({ status: "already-exited" });
    expect(existsSync(recordPath)).toBe(false);
  });
  test("a stale identity cannot stop an unrelated live child", async () => {
    if (process.platform !== "win32") return;
    const artifacts = mkdtempSync(join(tmpdir(), "factory-app-process-stale-test-"));
    const recordPath = appProcessRecordPath(artifacts);
    const child = spawnTestChild();
    try {
      const launcherIdentity = inspectAppProcessIdentity(child.pid);
      const staleLauncherIdentity = (BigInt(launcherIdentity) + 1n).toString();
      expect(() => recordAppProcess(child.pid, staleLauncherIdentity, recordPath)).toThrow("no longer matches");
      expect(existsSync(recordPath)).toBe(false);
      expect(processIsAlive(child.pid)).toBe(true);

      const record = recordAppProcess(child.pid, launcherIdentity, recordPath);
      writeFileSync(recordPath, JSON.stringify({ ...record, identity: (BigInt(record.identity) + 1n).toString() }));
      expect(stopRecordedApp(recordPath).status).toBe("identity-mismatch");
      expect(processIsAlive(child.pid)).toBe(true);
      expect(existsSync(recordPath)).toBe(true);
    } finally {
      rmSync(recordPath, { force: true });
      await stopTestChild(child);
    }
  });
  test("stops an owned live child and repeated cleanup cannot act on a later child", async () => {
    if (process.platform !== "win32") return;
    const artifacts = mkdtempSync(join(tmpdir(), "factory-app-process-owned-test-"));
    const recordPath = appProcessRecordPath(artifacts);
    const owned = spawnTestChild();
    recordAppProcess(owned.pid, inspectAppProcessIdentity(owned.pid), recordPath);
    expect(stopRecordedApp(recordPath)).toEqual({ status: "stopped" });
    await owned.exited;
    expect(processIsAlive(owned.pid)).toBe(false);
    expect(existsSync(recordPath)).toBe(false);

    const later = spawnTestChild();
    try {
      expect(stopRecordedApp(recordPath)).toEqual({ status: "no-record" });
      expect(processIsAlive(later.pid)).toBe(true);
    } finally {
      await stopTestChild(later);
    }
  });
  test("workflow cleanup uses the shared identity helper after checker failure", async () => {
    if (process.platform !== "win32") return;
    const artifacts = mkdtempSync(join(tmpdir(), "factory-app-process-finalizer-test-"));
    const recordPath = appProcessRecordPath(artifacts);
    const child = spawnTestChild();
    try {
      recordAppProcess(child.pid, inspectAppProcessIdentity(child.pid), recordPath);
      const cleanup = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "cleanup.ts")], {
        env: { ...process.env, ARTIFACTS_DIR: artifacts },
        stdout: "pipe",
        stderr: "pipe",
      });
      expect(cleanup.exitCode, cleanup.stderr.toString()).toBe(0);
      expect(JSON.parse(cleanup.stdout.toString())).toEqual({ cleaned: false, app_record: false, app_cleanup: "stopped" });
      await child.exited;
      expect(processIsAlive(child.pid)).toBe(false);
    } finally {
      if (processIsAlive(child.pid)) await stopTestChild(child);
    }
  });
  test("selects and publishes against the current encoded branch ref when the PR base snapshot is stale", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-current-base-test-"));
    const log = join(root, "gh.log");
    installFakeCommand(root, "gh", `import { appendFileSync } from "node:fs";
const args = process.argv.slice(2);
appendFileSync(process.env.GH_LOG!, JSON.stringify(args) + "\\n");
if (args[0] === "pr" && args[1] === "view") console.log(JSON.stringify({ number: 7, isDraft: true, createdAt: "2026-01-01", headRefOid: "head-a", baseRefName: "release/2026", baseRefOid: "stale-pr-base", files: [{ path: "public/app.css" }], body: "", state: "OPEN" }));
else if (args[0] === "api" && args[1] === "repos/example/shelf/git/ref/heads/release%2F2026") console.log(JSON.stringify({ object: { sha: "current-base" } }));
else if (args[0] === "api" && args[1] === "repos/example/shelf/issues/7/comments") console.log("[]");
else if (args[0] === "api" && args[1] === "--method" && args[2] === "POST" && ["repos/example/shelf/statuses/head-a", "repos/example/shelf/issues/7/comments"].includes(args[3])) {}
else throw new Error("unexpected gh arguments: " + JSON.stringify(args));
`);
    const common = isolatedCommandEnv(root, { GH_LOG: log, INPUTS_REPO_PATH: "example/shelf", INPUTS_PR: "7" });
    expect(Object.keys(common).filter(key => key.toUpperCase() === "PATH")).toEqual(["PATH"]);
    expect(common.PATH).toBe(root);
    const selected = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "select.ts")], { env: common, stdout: "pipe", stderr: "pipe" });
    expect(selected.exitCode, selected.stderr.toString()).toBe(0);
    expect(JSON.parse(selected.stdout.toString())).toMatchObject({ found: true, head: "head-a", base: "release/2026", base_oid: "current-base" });

    const artifacts = join(root, "artifacts");
    mkdirSync(join(artifacts, "app-check"), { recursive: true });
    const published = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "publish.ts")], {
      env: { ...common, ARTIFACTS_DIR: artifacts, INPUTS_HEAD: "head-a", INPUTS_BASE: "release/2026", INPUTS_BASE_OID: "current-base", INPUTS_OVERALL: "PASS", INPUTS_LINES: "[]" },
      stdout: "pipe", stderr: "pipe",
    });
    expect(published.exitCode, published.stderr.toString()).toBe(0);
    expect(JSON.parse(published.stdout.toString())).toEqual({ state: "success", comment: "created" });
    const calls = readFileSync(log, "utf8").trim().split("\n").map(line => JSON.parse(line) as string[]);
    expect(calls.filter(args => args[1] === "repos/example/shelf/git/ref/heads/release%2F2026")).toHaveLength(2);
    expect(calls.find(args => args.includes("repos/example/shelf/statuses/head-a"))).toContain("state=success");
  }, 20_000);
});
