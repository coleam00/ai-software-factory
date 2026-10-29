import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyFactoryConfig, DEFAULTS, parseFactoryConfig } from "../../../../scripts/factory-config.ts";

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
    expect(() => parseFactoryConfig("holdouts:\n  enabled: true\n  path: relative/scenarios\n")).toThrow("absolute external path");
    expect(() => parseFactoryConfig("models:\n  aliases:\n    manager:\n      model: x\n")).toThrow("start with @");
  });

  test("refuses a pre-existing non-Codex model binding", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-config-provider-test-"));
    const archon = join(root, ".archon");
    mkdirSync(archon, { recursive: true });
    writeFileSync(join(archon, "factory.yaml"), "{}\n");
    writeFileSync(join(archon, "config.yaml"), "aliases:\n  '@implementer': { provider: another-provider, model: model-x }\n");
    expect(() => applyFactoryConfig(root)).toThrow("must use provider codex");
  });

  test("applies Codex aliases, current trigger schema, and preserves custom policy prose", () => {
    const root = mkdtempSync(join(tmpdir(), "factory-config-test-"));
    const archon = join(root, ".archon"), policy = join(archon, "sdlc", "policy");
    mkdirSync(policy, { recursive: true });
    writeFileSync(join(policy, "triage.md"), "# Team triage\n\nKeep this project-specific rule.\n");
    writeFileSync(join(archon, "config.yaml"), "docs:\n  path: product-docs\naliases:\n  '@custom': { provider: codex, model: custom-model }\n");
    writeFileSync(join(archon, "factory.yaml"), `
project: { repo_host: github.com, repo_path: example/widgets }
autonomy: supervised
models:
  aliases:
    '@factory-manager': { model: manager-model, effort: high }
triggers:
  enabled: true
  host_id: test-host
  run_as_user_id: 123e4567-e89b-42d3-a456-426614174000
  build_queue: { interval_seconds: 0 }
  merge_queue: { interval_seconds: 0 }
  discoveries: { interval_seconds: 0 }
  manager: { interval_seconds: 900, capacity: 1 }
  app_check: { interval_seconds: 0 }
`);

    const result = applyFactoryConfig(root);
    const triage = readFileSync(join(policy, "triage.md"), "utf8");
    expect(triage).toContain("Keep this project-specific rule.");
    expect(triage).toContain("Preset: supervised");
    expect(result.triggers).toHaveLength(1);

    const config = Bun.YAML.parse(readFileSync(join(archon, "config.yaml"), "utf8")) as any;
    expect(config.assistant).toBe("codex");
    expect(config.docs.path).toBe("product-docs");
    expect(config.aliases["@custom"].model).toBe("custom-model");
    expect(config.aliases["@factory-manager"]).toEqual({ provider: "codex", model: "manager-model", effort: "high" });

    const trigger = JSON.parse(readFileSync(join(archon, "triggers", "factory-manager.json"), "utf8"));
    expect(trigger.version).toBe(1);
    expect(trigger.binding.bindingRevision).toBeNull();
    expect(trigger.binding.launch).toMatchObject({ workflowName: "factory-manager", isolation: { kind: "default" } });
    expect(trigger.schedule).toEqual({ intervalSeconds: 900, runAtLoad: false });
  });
});
