import { describe, expect, test } from "bun:test";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
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

    const fake = join(artifacts, "fake-gh.ts");
    const command = join(artifacts, process.platform === "win32" ? "gh.cmd" : "gh");
    writeFileSync(fake, 'if (process.argv[2] === "issue" && process.argv[3] === "list") console.log("[]");\n');
    if (process.platform === "win32") writeFileSync(command, `@"${process.execPath}" "${fake}" %*\r\n`);
    else {
      writeFileSync(command, `#!/bin/sh\nexec "${process.execPath}" "${fake}" "$@"\n`);
      chmodSync(command, 0o755);
    }
    writeFileSync(backlogPath, "\uFEFF[]");
    const published = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "publish.ts")], {
      env: { ...env, PATH: `${artifacts}${delimiter}${process.env.PATH ?? ""}`, INPUTS_REPO_PATH: "example/widgets" },
      stdout: "pipe", stderr: "pipe",
    });
    expect(published.exitCode, published.stderr.toString()).toBe(0);
    expect(JSON.parse(published.stdout.toString())).toEqual({ published: true, created: [], existing: [] });

    writeFileSync(backlogPath, '\uFEFF[{"key":');
    const malformed = Bun.spawnSync([process.execPath, join(import.meta.dir, "..", "scripts", "check.ts")], { env, stdout: "pipe", stderr: "pipe" });
    expect(malformed.exitCode).not.toBe(0);
  });
});
