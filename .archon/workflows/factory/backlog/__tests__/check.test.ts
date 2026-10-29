import { describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installFakeCommand, isolatedCommandEnv } from "../../.shared/test-command.ts";
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
  test("check and publish entrypoints accept a BOM backlog while malformed JSON still fails", () => {
    const artifacts = mkdtempSync(join(tmpdir(), "factory-backlog-entrypoint-test-"));
    const backlogPath = join(artifacts, "backlog.json");
    const tickets = [{ key: "first-run", title: "Run the core path", body: body("first-run"), depends_on: [], size: "s" }];
    writeFileSync(backlogPath, `\uFEFF${JSON.stringify(tickets)}`);
    const env = { ...process.env, ARTIFACTS_DIR: artifacts };
    const checked = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "check.ts")], { env, stdout: "pipe", stderr: "pipe" });
    expect(checked.exitCode, checked.stderr.toString()).toBe(0);
    expect(JSON.parse(checked.stdout.toString())).toEqual({ count: 1, keys: ["first-run"] });

    const log = join(artifacts, "gh.log");
    installFakeCommand(artifacts, "gh", `import { appendFileSync } from "node:fs";
const args = process.argv.slice(2);
appendFileSync(process.env.GH_LOG!, JSON.stringify(args) + "\\n");
if (args[0] === "issue" && args[1] === "list") console.log("[]");
else throw new Error("unexpected gh arguments: " + JSON.stringify(args));
`);
    writeFileSync(backlogPath, "\uFEFF[]");
    const published = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "publish.ts")], {
      env: isolatedCommandEnv(artifacts, { ARTIFACTS_DIR: artifacts, GH_LOG: log, INPUTS_REPO_PATH: "example/widgets" }),
      stdout: "pipe", stderr: "pipe",
    });
    expect(published.exitCode, published.stderr.toString()).toBe(0);
    expect(JSON.parse(published.stdout.toString())).toEqual({ published: true, created: [], existing: [] });
    const calls = readFileSync(log, "utf8").trim().split("\n").map(line => JSON.parse(line) as string[]);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.slice(0, 2)).toEqual(["issue", "list"]);

    writeFileSync(backlogPath, '\uFEFF[{"key":');
    const malformed = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "check.ts")], { env, stdout: "pipe", stderr: "pipe" });
    expect(malformed.exitCode).not.toBe(0);
  });
});
