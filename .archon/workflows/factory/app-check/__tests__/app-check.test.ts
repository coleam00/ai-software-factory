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
import { existsSync, mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-a", baseRefName: "main", baseRefOid: "base-a" }, "head-a", "main", "base-a")).toBe(true);
    expect(bindingIsCurrent({ state: "OPEN", headRefOid: "head-b", baseRefName: "main", baseRefOid: "base-a" }, "head-a", "main", "base-a")).toBe(false);
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
});
