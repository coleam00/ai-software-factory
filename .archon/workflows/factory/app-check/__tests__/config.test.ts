import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { applyFactoryConfig, DEFAULTS, parseFactoryConfig, replaceManagedPolicy } from "../../../../scripts/factory-config.ts";

describe("factory config", () => {
  test("defaults omitted optional keys without inventing an app command", () => {
    const config = parseFactoryConfig("app_check:\n  trigger: always\n");
    expect(config.app_check.trigger).toBe("always");
    expect(config.app_check.frontend_paths).toEqual(DEFAULTS.app_check.frontend_paths);
    expect(config.app.start).toBe("");
    expect(config.app_check.enabled).toBe(false);
  });

  test("rejects malformed and unknown settings", () => {
    expect(() => parseFactoryConfig("autonomy: reckless\n")).toThrow("expected one of");
    expect(() => parseFactoryConfig("mystery: true\n")).toThrow("unknown setting");
    expect(() => parseFactoryConfig("app_check:\n  enabled: true\n")).toThrow("app.start");
    expect(parseFactoryConfig("app_check:\n  enabled: true\napp:\n  runtime: C:/runtime/scenario.json\n  teardown: bash teardown.sh\n").app.runtime).toBe("C:/runtime/scenario.json");
    expect(() => parseFactoryConfig("app:\n  runtime: relative/scenario.json\n")).toThrow("app.runtime");
    expect(() => parseFactoryConfig("holdouts:\n  enabled: true\n  path: relative/scenarios\n")).toThrow("absolute external path");
    expect(() => parseFactoryConfig("models:\n  aliases:\n    manager:\n      provider: codex\n      model: x\n")).toThrow("start with @");
    expect(() => parseFactoryConfig("models:\n  aliases:\n    '@manager':\n      model: x\n")).toThrow("provider");
    expect(() => parseFactoryConfig("triggers:\n  manager:\n    capacity: 2\n")).toThrow("unknown setting");
    expect(() => parseFactoryConfig("models:\n  tiers:\n    medium: { provider: codex, model: x, effort: impossible }\n")).toThrow("expected one of");
  });

  test("rejects a normalized holdout path inside the target before writing", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-config-holdout-test-"));
    const archon = join(root, ".archon");
    mkdirSync(archon, { recursive: true });
    const inCheckout = resolve(root, "private", "..", "holdouts");
    writeFileSync(join(archon, "factory.yaml"), Bun.YAML.stringify({ holdouts: { enabled: true, path: inCheckout } }));
    expect(() => applyFactoryConfig(root)).toThrow("outside the target checkout");
    expect(() => readFileSync(join(archon, "config.yaml"), "utf8")).toThrow();
  });

  test("applies selected providers, current trigger shape, and preserves unrelated profiles and policy prose", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-config-test-"));
    const archon = join(root, ".archon"), policy = join(archon, "sdlc", "policy");
    mkdirSync(policy, { recursive: true });
    writeFileSync(join(policy, "triage.md"), "# Team triage\n\nKeep this project-specific rule.\n");
    writeFileSync(join(archon, "config.yaml"), "assistant: pi\ndocs:\n  path: product-docs\ntiers:\n  small: { provider: pi, model: inherited-small }\naliases:\n  '@custom': { provider: another-provider, model: custom-model }\n");
    writeFileSync(join(archon, "factory.yaml"), `
project: { repo_host: github.com, repo_path: example/widgets }
autonomy: supervised
models:
  aliases:
    '@factory-manager': { provider: claude, model: manager-model, effort: persistent }
triggers:
  enabled: true
  host_id: test-host
  run_as_user_id: e58d38eaf1528395ba6abc0da3967127
  build_queue: { interval_seconds: 0 }
  merge_queue: { interval_seconds: 0 }
  discoveries: { interval_seconds: 0 }
  manager: { interval_seconds: 900 }
  app_check: { interval_seconds: 900 }
`);

    const result = applyFactoryConfig(root);
    const triage = readFileSync(join(policy, "triage.md"), "utf8");
    expect(triage).toContain("Keep this project-specific rule.");
    expect(triage).toContain("Preset: supervised");
    expect(result.triggers).toHaveLength(2);

    const config = Bun.YAML.parse(readFileSync(join(archon, "config.yaml"), "utf8")) as any;
    expect(config.assistant).toBe("pi");
    expect(config.docs.path).toBe("product-docs");
    expect(config.tiers.small).toEqual({ provider: "pi", model: "inherited-small" });
    expect(config.aliases["@custom"]).toEqual({ provider: "another-provider", model: "custom-model" });
    expect(config.aliases["@custom"].model).toBe("custom-model");
    expect(config.aliases["@factory-manager"]).toEqual({ provider: "claude", model: "manager-model", effort: "persistent" });

    const trigger = JSON.parse(readFileSync(join(archon, "triggers", "factory-manager.json"), "utf8"));
    expect(trigger.version).toBe(1);
    expect(trigger.binding.bindingRevision).toBeNull();
    expect(trigger.binding.runAsUserId).toBe("e58d38eaf1528395ba6abc0da3967127");
    expect(trigger.binding.capacity).toBe(1);
    expect(trigger.binding.launch).toMatchObject({ workflowName: "factory-manager", isolation: { kind: "default" } });
    expect(trigger.schedule).toEqual({ intervalSeconds: 900, runAtLoad: false });
    const appCheck = JSON.parse(readFileSync(join(archon, "triggers", "factory-app-check.json"), "utf8"));
    expect(appCheck.binding.capacity).toBe(1);
    expect(appCheck.sourceInstanceId).not.toBe(trigger.sourceInstanceId);
  });

  test("keeps project-specific trigger identities stable and distinct", () => {
    const create = (repo: string) => {
      const root = mkdtempSync(join(tmpdir(), "factory-identity-test-")), archon = join(root, ".archon");
      mkdirSync(archon, { recursive: true });
      writeFileSync(join(archon, "factory.yaml"), Bun.YAML.stringify({
        project: { repo_host: "github.com", repo_path: repo },
        triggers: { enabled: true, host_id: "test-host", run_as_user_id: "e58d38eaf1528395ba6abc0da3967127", manager: { interval_seconds: 900 } },
      }));
      applyFactoryConfig(root);
      const path = join(archon, "triggers", "factory-manager.json");
      const first = JSON.parse(readFileSync(path, "utf8")).sourceInstanceId;
      applyFactoryConfig(root);
      expect(JSON.parse(readFileSync(path, "utf8")).sourceInstanceId).toBe(first);
      return first;
    };
    expect(create("example/widgets")).not.toBe(create("example/widgets"));
  });

  test("presets differ while managed replacement preserves surrounding prose", () => {
    const original = "# Team policy\n\nBefore text.\n\nAfter text.\n";
    const rendered = (["supervised", "standard", "autonomous"] as const).map(preset => replaceManagedPolicy(original, preset, "merge"));
    expect(new Set(rendered).size).toBe(3);
    for (const text of rendered) {
      expect(text).toContain("Before text.");
      expect(text).toContain("After text.");
    }
    const updated = replaceManagedPolicy(rendered[0]!, "autonomous", "merge");
    expect(updated).toContain("Before text.");
    expect(updated).toContain("After text.");
    expect(updated).toContain("Preset: autonomous");
    expect(() => replaceManagedPolicy("<!-- FACTORY AUTONOMY: START (managed by factory-setup) -->\n", "standard", "merge")).toThrow("malformed");
  });
});
