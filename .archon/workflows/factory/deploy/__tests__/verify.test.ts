import { expect, test } from "bun:test";
import { mkdtempSync, readFileSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { recordFailedDeployment, verifyDeployment } from "../scripts/verify.ts";

test("deployment verification accepts fresh exact evidence and rejects a revision mismatch", () => {
  const root = mkdtempSync(join(tmpdir(), "factory-deploy-test-"));
  const startedAt = Date.now(), report = join(root, "report.md"), health = join(root, "health.txt");
  for (const file of [report, health]) {
    writeFileSync(file, "observed");
    utimesSync(file, new Date(startedAt + 1000), new Date(startedAt + 1000));
  }
  const revision = "0123456789abcdef0123456789abcdef01234567";
  expect(verifyDeployment(revision, revision, revision, "healthy", [report, health], startedAt).deployed).toBe(true);
  expect(() => verifyDeployment(revision, revision, "ffffffffffffffffffffffffffffffffffffffff", "healthy", [report, health], startedAt)).toThrow("revision mismatch");
});

test("a failed deployment leaves one record the discoveries workflow can read", () => {
  const root = mkdtempSync(join(tmpdir(), "factory-deploy-test-"));
  const revision = "0123456789abcdef0123456789abcdef01234567";
  recordFailedDeployment(root, revision, "deployment health check was unhealthy", ["report.md"]);
  const records = JSON.parse(readFileSync(join(root, "discoveries", "deploy.json"), "utf8"));
  expect(records).toHaveLength(1);
  expect(records[0].title).toBe("Deploying 0123456 failed");
  expect(records[0].claim).toBe("deployment health check was unhealthy");
  expect(records[0].evidence).toContain("report.md");
  expect(records[0].relation).toBe("unrelated");
});
