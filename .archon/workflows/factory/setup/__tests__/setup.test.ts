import { describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyCi, applyFactoryConfig, applyMaintainers, CI_PATH, MAINTAINERS_PATH, parseFactoryConfig } from "../../../../scripts/factory-config.ts";
import { deliveriesFor, type IssueEvent, type IssueSummary } from "../../../../scripts/intake-poll.ts";
import { cronLine, projectId, timersFor, windowsLauncher, windowsTaskXml } from "../../../../scripts/factory-schedule.ts";
import { checkState } from "../../../../scripts/factory-ready.ts";

const project = () => {
  const root = mkdtempSync(join(tmpdir(), "factory-setup-test-"));
  mkdirSync(join(root, ".archon"), { recursive: true });
  return root;
};

describe("maintainers", () => {
  test("parses, rejects non-logins, and defaults to none", () => {
    expect(parseFactoryConfig("").maintainers).toEqual([]);
    expect(parseFactoryConfig("maintainers: [alice, bob]\n").maintainers).toEqual(["alice", "bob"]);
    expect(() => parseFactoryConfig("maintainers: ['two words']\n")).toThrow("not a forge login");
  });

  test("adds configured logins and keeps existing entries, including opt-in objects", () => {
    const root = project();
    expect(applyMaintainers(root, [])).toBe("");
    expect(existsSync(join(root, MAINTAINERS_PATH))).toBe(false);
    mkdirSync(join(root, ".archon", "sdlc", "policy"), { recursive: true });
    writeFileSync(join(root, MAINTAINERS_PATH), JSON.stringify([{ login: "Alice", merge_queue: "own-prs" }]));
    applyMaintainers(root, ["alice", "bob"]);
    expect(JSON.parse(readFileSync(join(root, MAINTAINERS_PATH), "utf8"))).toEqual([{ login: "Alice", merge_queue: "own-prs" }, "bob"]);
  });
});

describe("ci", () => {
  test("writes the workflow only when the repo has none", () => {
    const root = project();
    expect(applyCi(root, false)).toBe("");
    expect(applyCi(root, true)).toContain("factory-ci.yml");
    const text = readFileSync(join(root, CI_PATH), "utf8");
    expect(text).toContain("scripts/test");
    expect(Bun.YAML.parse(text)).toMatchObject({ name: "ci", jobs: { tests: { "runs-on": "ubuntu-latest" } } });

    const other = project();
    mkdirSync(join(other, ".github", "workflows"), { recursive: true });
    writeFileSync(join(other, ".github", "workflows", "mine.yml"), "name: mine\n");
    expect(applyCi(other, true)).toBe("");
    expect(existsSync(join(other, CI_PATH))).toBe(false);
  });
});

describe("factory-setup apply", () => {
  test("leaves an unchanged config.yaml alone and keeps CRLF policy files CRLF", () => {
    const root = project();
    const comments = "# Factory workflows inherit Archon's provider.\n# Another comment.\n";
    writeFileSync(join(root, ".archon", "config.yaml"), comments);
    mkdirSync(join(root, ".archon", "sdlc", "policy"), { recursive: true });
    writeFileSync(join(root, ".archon", "sdlc", "policy", "merge.md"), "# Merge policy\r\n\r\nOur rule.\r\n");
    writeFileSync(join(root, ".archon", "factory.yaml"), "project: { repo_path: example/widgets }\nmaintainers: [alice]\n");

    const result = applyFactoryConfig(root);
    expect(readFileSync(join(root, ".archon", "config.yaml"), "utf8")).toBe(comments);
    const merge = readFileSync(join(root, ".archon", "sdlc", "policy", "merge.md"), "utf8");
    expect(merge).toContain("Our rule.");
    expect(merge).toContain("Preset: standard");
    expect(merge.replace(/\r\n/g, "")).not.toContain("\n");
    expect(result.maintainers).toContain("maintainers.json");
    expect(result.ci).toContain("factory-ci.yml");

    const again = readFileSync(join(root, ".archon", "sdlc", "policy", "merge.md"), "utf8");
    applyFactoryConfig(root);
    expect(readFileSync(join(root, ".archon", "sdlc", "policy", "merge.md"), "utf8")).toBe(again);
  });

  test("writes an intake binding that queues instead of skipping", () => {
    const root = project();
    writeFileSync(join(root, ".archon", "factory.yaml"), `
project: { repo_path: example/widgets }
triggers:
  enabled: true
  host_id: test-host
  run_as_user_id: e58d38eaf1528395ba6abc0da3967127
`);
    const result = applyFactoryConfig(root);
    expect(result.triggers.some(path => path.endsWith("intake.json"))).toBe(true);
    const intake = JSON.parse(readFileSync(join(root, ".archon", "triggers", "intake.json"), "utf8"));
    expect(intake.binding).toMatchObject({ overlap: "queue", resource: "forge:github.com/example/widgets:intake", launch: { workflowName: "archon-intake", inputs: {} } });
    expect(intake.schedule.intervalSeconds).toBe(300);
  });
});

describe("intake poll", () => {
  const since = "2026-11-05T10:00:00Z";
  const issue = (number: number, extra: Partial<IssueSummary> = {}): IssueSummary => ({ number, title: `Issue ${number}`, body: "Do the thing.", state: "open", created_at: since, ...extra });

  test("one stable delivery per issue text, none for pull requests or closed issues", () => {
    const first = deliveriesFor("github.com", "a/b", [issue(1), issue(2, { pull_request: {} }), issue(3, { state: "closed" })], [], since);
    expect(first).toHaveLength(1);
    expect(first[0]!.inputs).toMatchObject({ number: 1, kind: "issue", action: "opened", repo_path: "a/b" });
    const again = deliveriesFor("github.com", "a/b", [issue(1)], [], since);
    expect(again[0]!.deliveryId).toBe(first[0]!.deliveryId);
    const edited = deliveriesFor("github.com", "a/b", [issue(1, { body: "Do the other thing." })], [], since);
    expect(edited[0]!.deliveryId).not.toBe(first[0]!.deliveryId);
  });

  test("reports intake labels with who added them, and nothing else", () => {
    const event = (extra: Partial<IssueEvent>): IssueEvent => ({ id: 7, event: "labeled", created_at: "2026-11-05T10:05:00Z", actor: { login: "alice" }, label: { name: "archon-triage" }, issue: { number: 4, state: "open" }, ...extra });
    const found = deliveriesFor("github.com", "a/b", [], [
      event({}),
      event({ id: 8, label: { name: "bug" } }),
      event({ id: 9, created_at: "2026-11-05T09:00:00Z" }),
      event({ id: 10, issue: { number: 5, state: "open", pull_request: {} } }),
      event({ id: 11, event: "unlabeled" }),
      event({ id: 12, actor: null }),
    ], since);
    expect(found).toEqual([{ deliveryId: "label:7", inputs: { repo_host: "github.com", repo_path: "a/b", kind: "issue", number: 4, action: "added", label: "archon-triage", label_actor: "alice" } }]);
  });
});

describe("factory-schedule", () => {
  test("one timer per binding, the intake one runs the poll script", () => {
    const root = project();
    writeFileSync(join(root, ".archon", "factory.yaml"), `
project: { repo_path: example/widgets }
triggers:
  enabled: true
  host_id: test-host
  run_as_user_id: e58d38eaf1528395ba6abc0da3967127
  discoveries: { interval_seconds: 0 }
  manager: { interval_seconds: 0 }
  app_check: { interval_seconds: 0 }
`);
    applyFactoryConfig(root);
    const timers = timersFor(root, { archon: "/bin/archon", bun: "/bin/bun" });
    expect(timers.map(t => t.name).sort()).toEqual(["build-queue", "intake", "merge-queue"]);
    expect(timers.find(t => t.name === "intake")!.command.slice(0, 2)).toEqual(["/bin/bun", join(root, ".archon", "scripts", "intake-poll.ts")]);
    expect(timers.find(t => t.name === "build-queue")!.command).toEqual(["/bin/archon", "trigger", "fire", "--config", join(root, ".archon", "triggers", "build-queue.json")]);
  });

  test("ids differ per checkout and names stay readable", () => {
    expect(projectId("C:/a", "coleam00/My_Repo")).toMatch(/^coleam00-my-repo-[0-9a-f]{6}$/);
    expect(projectId("C:/a", "x/y")).not.toBe(projectId("C:/b", "x/y"));
  });

  test("Windows task runs hidden through wscript, repeats, never overlaps", () => {
    const xml = windowsTaskXml("C:\\Users\\me\\.archon\\factory-timers\\x\\intake.vbs", 300, new Date("2026-11-05T10:00:00Z"));
    expect(xml).toContain("<Interval>PT5M</Interval>");
    expect(xml).toContain("<StartBoundary>2026-11-05T10:01:00Z</StartBoundary>");
    expect(xml).toContain("<Command>wscript.exe</Command>");
    expect(xml).toContain("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>");
    expect(xml).toContain("&quot;C:\\Users\\me\\.archon\\factory-timers\\x\\intake.vbs&quot;");
    const vbs = windowsLauncher("C:\\work\\app", ["C:\\Program Files\\bun.exe", "C:\\work\\app\\.archon\\scripts\\intake-poll.ts"], "C:\\logs\\intake.log", { GH_BIN: "C:\\gh.exe" });
    expect(vbs).toContain('shell.Run("cmd /c """"C:\\Program Files\\bun.exe"" C:\\work\\app\\.archon\\scripts\\intake-poll.ts >> C:\\logs\\intake.log 2>&1""", 0, True)');
    expect(vbs).toContain('shell.Environment("Process")("GH_BIN") = "C:\\gh.exe"');
  });

  test("cron lines carry a marker so remove only touches this project", () => {
    const line = cronLine("p-1", "/srv/app", { name: "intake", intervalSeconds: 300, command: ["/bin/bun", "/srv/app/.archon/scripts/intake-poll.ts"] }, "/tmp/i.log");
    expect(line.startsWith("*/5 * * * * cd '/srv/app' && '/bin/bun'")).toBe(true);
    expect(line.endsWith("# archon-factory:p-1:intake")).toBe(true);
    expect(cronLine("p", "/a", { name: "x", intervalSeconds: 14400, command: ["/a"] }, "/l").startsWith("0 */4 * * *")).toBe(true);
  });
});

describe("factory-ready", () => {
  test("reads checks the way the merge queue does", () => {
    expect(checkState([], [])).toBe("none");
    expect(checkState([{ status: "completed", conclusion: "success" }], [])).toBe("green");
    expect(checkState([{ status: "in_progress", conclusion: null }], [])).toBe("pending");
    expect(checkState([{ status: "completed", conclusion: "failure" }], [{ state: "success" }])).toBe("red");
    expect(checkState([], [{ state: "pending" }])).toBe("pending");
  });
});
