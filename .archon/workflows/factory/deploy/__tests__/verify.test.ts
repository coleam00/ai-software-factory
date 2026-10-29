import { expect, test } from "bun:test";
import { mkdtempSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { verifyDeployment } from "../scripts/verify.ts";

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
