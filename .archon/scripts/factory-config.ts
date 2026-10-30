import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

export type Depth = "auto" | "light" | "normal" | "deep";
export type AutonomyPreset = "supervised" | "standard" | "autonomous";
export type Effort = "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | "persistent";

export interface ModelProfile { provider: string; model: string; effort?: Effort }
export interface ScheduleConfig { interval_seconds: number }
export interface FactoryConfig {
  project: { repo_host: string; repo_path: string };
  app_check: {
    enabled: boolean;
    trigger: "always" | "frontend" | "never";
    frontend_paths: string[];
    depth: Depth;
  };
  holdouts: { enabled: boolean; path: string };
  protected_paths: string[];
  autonomy: AutonomyPreset;
  app: { prepare: string; start: string; port_env: string; database_env: string; health_path: string; runtime: string; teardown: string };
  models: {
    assistant: string;
    tiers: Partial<Record<"small" | "medium" | "large", ModelProfile>>;
    aliases: Record<string, ModelProfile>;
  };
  triggers: {
    enabled: boolean;
    host_id: string;
    run_as_user_id: string;
    build_queue: ScheduleConfig;
    merge_queue: ScheduleConfig;
    discoveries: ScheduleConfig;
    manager: ScheduleConfig;
    app_check: ScheduleConfig;
  };
}

export const DEFAULTS: FactoryConfig = {
  project: { repo_host: "github.com", repo_path: "" },
  app_check: {
    enabled: false,
    trigger: "frontend",
    frontend_paths: ["app/**", "public/**", "src/**/*.css", "src/**/*.html", "src/**/*.tsx", "src/**/*.jsx"],
    depth: "auto",
  },
  holdouts: { enabled: false, path: "" },
  protected_paths: [".archon/**", ".github/**", "DEPLOY.md"],
  autonomy: "standard",
  app: { prepare: "", start: "", port_env: "PORT", database_env: "", health_path: "/health", runtime: "", teardown: "" },
  models: { assistant: "", tiers: {}, aliases: {} },
  triggers: {
    enabled: false,
    host_id: "",
    run_as_user_id: "",
    build_queue: { interval_seconds: 1800 },
    merge_queue: { interval_seconds: 1800 },
    discoveries: { interval_seconds: 14400 },
    manager: { interval_seconds: 14400 },
    app_check: { interval_seconds: 1800 },
  },
};

type RecordValue = Record<string, unknown>;
const EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent"] as const;
const TIERS = ["small", "medium", "large"] as const;
const SCHEDULES = ["build_queue", "merge_queue", "discoveries", "manager", "app_check"] as const;

function fail(path: string, message: string): never { throw new Error(`Invalid factory config at ${path}: ${message}`); }

function object(value: unknown, path: string): RecordValue {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail(path, "expected a mapping");
  return value as RecordValue;
}

function section(parent: RecordValue, key: string, path = key): RecordValue {
  return parent[key] === undefined ? {} : object(parent[key], path);
}

function exact(value: RecordValue, allowed: readonly string[], path: string): void {
  const unknown = Object.keys(value).filter(key => !allowed.includes(key));
  if (unknown.length) fail(path, `unknown setting(s): ${unknown.join(", ")}`);
}

function string(value: unknown, fallback: string, path: string, allowEmpty = true): string {
  if (value === undefined) {
    if (!allowEmpty && !fallback.trim()) fail(path, "expected a non-empty string");
    return fallback;
  }
  if (typeof value !== "string" || (!allowEmpty && !value.trim())) fail(path, "expected a non-empty string");
  return value;
}

function boolean(value: unknown, fallback: boolean, path: string): boolean {
  if (value === undefined) return fallback;
  if (typeof value !== "boolean") fail(path, "expected true or false");
  return value;
}

function integer(value: unknown, fallback: number, path: string, minimum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || (value as number) < minimum) fail(path, `expected an integer >= ${minimum}`);
  return value as number;
}

function strings(value: unknown, fallback: string[], path: string): string[] {
  if (value === undefined) return [...fallback];
  if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim()))
    fail(path, "expected a list of non-empty strings");
  return [...new Set(value as string[])];
}

function choice<T extends string>(value: unknown, allowed: readonly T[], fallback: T, path: string): T {
  if (value === undefined) return fallback;
  if (typeof value !== "string" || !allowed.includes(value as T)) fail(path, `expected one of: ${allowed.join(", ")}`);
  return value as T;
}

function profile(value: unknown, path: string): ModelProfile {
  const raw = object(value, path);
  exact(raw, ["provider", "model", "effort"], path);
  const provider = string(raw.provider, "", `${path}.provider`, false);
  const model = string(raw.model, "", `${path}.model`, false);
  const effort = raw.effort === undefined ? undefined : choice(raw.effort, EFFORTS, "medium", `${path}.effort`);
  return effort ? { provider, model, effort } : { provider, model };
}

function profiles(value: unknown, path: string, aliases: boolean): Record<string, ModelProfile> {
  if (value === undefined) return {};
  const raw = object(value, path), result: Record<string, ModelProfile> = {};
  for (const [name, entry] of Object.entries(raw)) {
    if (aliases ? !name.startsWith("@") : !TIERS.includes(name as typeof TIERS[number]))
      fail(`${path}.${name}`, aliases ? "alias names must start with @" : `tier must be one of: ${TIERS.join(", ")}`);
    result[name] = profile(entry, `${path}.${name}`);
  }
  return result;
}

function schedule(value: unknown, fallback: ScheduleConfig, path: string): ScheduleConfig {
  const raw = value === undefined ? {} : object(value, path);
  exact(raw, ["interval_seconds"], path);
  return {
    interval_seconds: integer(raw.interval_seconds, fallback.interval_seconds, `${path}.interval_seconds`, 0),
  };
}

export function parseFactoryConfig(raw: string): FactoryConfig {
  let parsed: unknown;
  try { parsed = Bun.YAML.parse(raw); }
  catch (error) { throw new Error(`Invalid factory config YAML: ${error instanceof Error ? error.message : String(error)}`); }
  const root = object(parsed ?? {}, "root");
  exact(root, ["project", "app_check", "holdouts", "protected_paths", "autonomy", "app", "models", "triggers"], "root");

  const project = section(root, "project");
  exact(project, ["repo_host", "repo_path"], "project");
  const appCheck = section(root, "app_check");
  exact(appCheck, ["enabled", "trigger", "frontend_paths", "depth"], "app_check");
  const holdouts = section(root, "holdouts");
  exact(holdouts, ["enabled", "path"], "holdouts");
  const app = section(root, "app");
  exact(app, ["prepare", "start", "port_env", "database_env", "health_path", "runtime", "teardown"], "app");
  const models = section(root, "models");
  exact(models, ["assistant", "tiers", "aliases"], "models");
  const triggers = section(root, "triggers");
  exact(triggers, ["enabled", "host_id", "run_as_user_id", ...SCHEDULES], "triggers");

  const config: FactoryConfig = {
    project: {
      repo_host: string(project.repo_host, DEFAULTS.project.repo_host, "project.repo_host", false),
      repo_path: string(project.repo_path, DEFAULTS.project.repo_path, "project.repo_path"),
    },
    app_check: {
      enabled: boolean(appCheck.enabled, DEFAULTS.app_check.enabled, "app_check.enabled"),
      trigger: choice(appCheck.trigger, ["always", "frontend", "never"], DEFAULTS.app_check.trigger, "app_check.trigger"),
      frontend_paths: strings(appCheck.frontend_paths, DEFAULTS.app_check.frontend_paths, "app_check.frontend_paths"),
      depth: choice(appCheck.depth, ["auto", "light", "normal", "deep"], DEFAULTS.app_check.depth, "app_check.depth"),
    },
    holdouts: {
      enabled: boolean(holdouts.enabled, DEFAULTS.holdouts.enabled, "holdouts.enabled"),
      path: string(holdouts.path, DEFAULTS.holdouts.path, "holdouts.path"),
    },
    protected_paths: strings(root.protected_paths, DEFAULTS.protected_paths, "protected_paths"),
    autonomy: choice(root.autonomy, ["supervised", "standard", "autonomous"], DEFAULTS.autonomy, "autonomy"),
    app: {
      prepare: string(app.prepare, DEFAULTS.app.prepare, "app.prepare"),
      start: string(app.start, DEFAULTS.app.start, "app.start"),
      port_env: string(app.port_env, DEFAULTS.app.port_env, "app.port_env", false),
      database_env: string(app.database_env, DEFAULTS.app.database_env, "app.database_env"),
      health_path: string(app.health_path, DEFAULTS.app.health_path, "app.health_path", false),
      runtime: string(app.runtime, DEFAULTS.app.runtime, "app.runtime"),
      teardown: string(app.teardown, DEFAULTS.app.teardown, "app.teardown"),
    },
    models: {
      assistant: string(models.assistant, DEFAULTS.models.assistant, "models.assistant"),
      tiers: profiles(models.tiers, "models.tiers", false),
      aliases: profiles(models.aliases, "models.aliases", true),
    },
    triggers: {
      enabled: boolean(triggers.enabled, DEFAULTS.triggers.enabled, "triggers.enabled"),
      host_id: string(triggers.host_id, DEFAULTS.triggers.host_id, "triggers.host_id"),
      run_as_user_id: string(triggers.run_as_user_id, DEFAULTS.triggers.run_as_user_id, "triggers.run_as_user_id"),
      build_queue: schedule(triggers.build_queue, DEFAULTS.triggers.build_queue, "triggers.build_queue"),
      merge_queue: schedule(triggers.merge_queue, DEFAULTS.triggers.merge_queue, "triggers.merge_queue"),
      discoveries: schedule(triggers.discoveries, DEFAULTS.triggers.discoveries, "triggers.discoveries"),
      manager: schedule(triggers.manager, DEFAULTS.triggers.manager, "triggers.manager"),
      app_check: schedule(triggers.app_check, DEFAULTS.triggers.app_check, "triggers.app_check"),
    },
  };

  if (!config.app.health_path.startsWith("/")) fail("app.health_path", "must start with /");
  if (config.app_check.enabled && !config.app.start.trim() && !config.app.runtime.trim())
    fail("app.start", "or app.runtime is required when app_check.enabled is true");
  if (config.app.runtime.trim() && !isAbsolute(config.app.runtime.replace(/^~(?=[/\\]|$)/, homedir())))
    fail("app.runtime", "must be an absolute path to the runtime instructions (or start with ~)");
  if (config.holdouts.enabled) {
    if (!config.holdouts.path.trim()) fail("holdouts.path", "is required when holdouts.enabled is true");
    const expanded = config.holdouts.path.replace(/^~(?=[/\\]|$)/, homedir());
    if (!isAbsolute(expanded)) fail("holdouts.path", "must be an absolute external path (or start with ~)");
  }
  if (config.triggers.enabled) {
    if (!config.project.repo_path.trim() || !/^[^/\s]+\/[^/\s]+$/.test(config.project.repo_path))
      fail("project.repo_path", "must be OWNER/REPO when triggers are enabled");
    if (!config.triggers.host_id.trim()) fail("triggers.host_id", "is required when triggers are enabled");
    if (!config.triggers.run_as_user_id.trim())
      fail("triggers.run_as_user_id", "is required when triggers are enabled; use the opaque ID printed by archon trigger whoami");
  }
  return config;
}

export function readFactoryConfig(path = resolve(".archon/factory.yaml")): FactoryConfig {
  try { return parseFactoryConfig(readFileSync(path, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return structuredClone(DEFAULTS);
    throw error;
  }
}

const POLICY_MARKER_START = "<!-- FACTORY AUTONOMY: START (managed by factory-setup) -->";
const POLICY_MARKER_END = "<!-- FACTORY AUTONOMY: END -->";

const PRESET_POLICY: Record<AutonomyPreset, Record<"triage" | "build" | "merge", string[]>> = {
  supervised: {
    triage: ["Route only work already inside explicit product scope; ask on every ambiguity and never infer new product scope."],
    build: ["Ask before any material implementation choice, protected-path change, dependency change, or scope adjustment."],
    merge: ["Never merge without an explicit maintainer answer for that exact reviewed head; deployment is separately opted in."],
  },
  standard: {
    triage: ["Route clear work inside explicit product scope; ask when policy leaves scope or product intent ambiguous."],
    build: ["Proceed on clear in-scope implementation; ask on protected paths, new dependencies, product choices, or scope changes."],
    merge: ["Merge only when written merge policy clearly permits it and evidence is fresh for the exact head; otherwise ask."],
  },
  autonomous: {
    triage: ["Route clear work inside explicit product scope without asking; ambiguity never grants new product scope."],
    build: ["Proceed on clear in-scope implementation, but ask on protected paths, product-scope changes, and authority not stated in policy."],
    merge: ["Merge clear policy-approved work with fresh evidence for the exact head; deployment and protected paths still require explicit authority."],
  },
};

export function replaceManagedPolicy(existing: string, preset: AutonomyPreset, topic: "triage" | "build" | "merge"): string {
  const starts = existing.split(POLICY_MARKER_START).length - 1;
  const ends = existing.split(POLICY_MARKER_END).length - 1;
  if (starts !== ends || starts > 1) throw new Error(`Cannot update ${topic} policy: malformed factory managed block`);
  const body = [POLICY_MARKER_START, `Preset: ${preset}`, ...PRESET_POLICY[preset][topic].map(line => `- ${line}`), POLICY_MARKER_END].join("\n");
  if (starts === 1) {
    const start = existing.indexOf(POLICY_MARKER_START), end = existing.indexOf(POLICY_MARKER_END) + POLICY_MARKER_END.length;
    return `${existing.slice(0, start)}${body}${existing.slice(end)}`;
  }
  return `${existing.trimEnd()}${existing.trim() ? "\n\n" : ""}${body}\n`;
}

function applyPolicies(root: string, preset: AutonomyPreset): string[] {
  const directory = join(root, ".archon", "sdlc", "policy");
  mkdirSync(directory, { recursive: true });
  return (["triage", "build", "merge"] as const).map(topic => {
    const path = join(directory, `${topic}.md`);
    const existing = existsSync(path) ? readFileSync(path, "utf8") : `# ${topic[0]!.toUpperCase()}${topic.slice(1)} policy\n\n`;
    writeFileSync(path, replaceManagedPolicy(existing, preset, topic));
    return path;
  });
}

function archonProfile(value: ModelProfile): RecordValue {
  return { provider: value.provider, model: value.model, ...(value.effort ? { effort: value.effort } : {}) };
}

function applyArchonConfig(root: string, factory: FactoryConfig): string {
  const path = join(root, ".archon", "config.yaml");
  const current = existsSync(path) ? object(Bun.YAML.parse(readFileSync(path, "utf8")) ?? {}, ".archon/config.yaml") : {};
  const aliases = current.aliases === undefined ? {} : object(current.aliases, ".archon/config.yaml aliases");
  const tiers = current.tiers === undefined ? {} : object(current.tiers, ".archon/config.yaml tiers");
  for (const tier of TIERS) {
    const configured = factory.models.tiers[tier];
    if (configured) tiers[tier] = archonProfile(configured);
  }
  for (const [name, configured] of Object.entries(factory.models.aliases)) aliases[name] = archonProfile(configured);
  if (factory.models.assistant) current.assistant = factory.models.assistant;
  if (Object.keys(tiers).length) current.tiers = tiers;
  if (Object.keys(aliases).length) current.aliases = aliases;
  mkdirSync(join(root, ".archon"), { recursive: true });
  writeFileSync(path, `${Bun.YAML.stringify(current).trim()}\n`);
  return path;
}

interface TimerDefinition {
  key: typeof SCHEDULES[number];
  workflow: string;
  binding: string;
  resource: string;
  overlap: "skip" | "queue";
  inputs: Record<string, string>;
  isolation: "default" | "worktree";
}

function triggerDefinitions(config: FactoryConfig): TimerDefinition[] {
  const repo = `${config.project.repo_host}/${config.project.repo_path}`;
  const inputs = { repo_host: config.project.repo_host, repo_path: config.project.repo_path };
  return [
    { key: "build_queue", workflow: "archon-build-queue", binding: "build-queue", resource: `forge:${repo}:build`, overlap: "skip", inputs, isolation: "worktree" },
    { key: "merge_queue", workflow: "archon-merge-queue", binding: "merge-queue", resource: `forge:${repo}:merge`, overlap: "skip", inputs, isolation: "default" },
    { key: "discoveries", workflow: "archon-discoveries", binding: "discoveries", resource: `discoveries:${repo}`, overlap: "skip", inputs, isolation: "default" },
    { key: "manager", workflow: "factory-manager", binding: "factory-manager", resource: `factory:${repo}:manager`, overlap: "skip", inputs: { repo_path: config.project.repo_path }, isolation: "default" },
    { key: "app_check", workflow: "factory-app-check", binding: "factory-app-check", resource: `factory:${repo}:app-check`, overlap: "skip", inputs: { repo_path: config.project.repo_path }, isolation: "default" },
  ];
}

function projectBindingPrefix(root: string, config: FactoryConfig): string {
  const target = process.platform === "win32" ? resolve(root).toLowerCase() : resolve(root);
  const identity = JSON.stringify([target, config.project.repo_host, config.project.repo_path]);
  return `factory-${createHash("sha256").update(identity).digest("hex").slice(0, 16)}`;
}

function applyTriggers(root: string, config: FactoryConfig): string[] {
  const directory = join(root, ".archon", "triggers"), written: string[] = [], projectId = projectBindingPrefix(root, config);
  for (const definition of triggerDefinitions(config)) {
    const path = join(directory, `${definition.binding}.json`), schedule = config.triggers[definition.key];
    if (!config.triggers.enabled || schedule.interval_seconds === 0) {
      if (existsSync(path)) rmSync(path);
      continue;
    }
    mkdirSync(directory, { recursive: true });
    const document = {
      version: 1,
      sourceInstanceId: `${projectId}-${definition.binding}`,
      binding: {
        bindingId: definition.binding,
        bindingRevision: null,
        hostId: config.triggers.host_id,
        runAsUserId: config.triggers.run_as_user_id,
        resource: definition.resource,
        capacity: 1,
        overlap: definition.overlap,
        launch: {
          cwd: resolve(root),
          workflowName: definition.workflow,
          inputs: definition.inputs,
          isolation: { kind: definition.isolation },
        },
      },
      schedule: { intervalSeconds: schedule.interval_seconds, runAtLoad: false },
    };
    writeFileSync(path, `${JSON.stringify(document, null, 2)}\n`);
    written.push(path);
  }
  return written;
}

export interface ApplyResult { config: string; policies: string[]; triggers: string[]; autonomy: AutonomyPreset }

export function assertHoldoutsOutsideRoot(root: string, factory: FactoryConfig): void {
  if (!factory.holdouts.enabled) return;
  const checkout = resolve(root);
  const holdouts = resolve(factory.holdouts.path.replace(/^~(?=[/\\]|$)/, homedir()));
  const boundary = relative(checkout, holdouts);
  if (boundary === "" || (boundary !== ".." && !boundary.startsWith(`..${sep}`) && !isAbsolute(boundary)))
    fail("holdouts.path", "must resolve outside the target checkout");
}

export function applyFactoryConfig(root = resolve(".")): ApplyResult {
  const factory = readFactoryConfig(join(root, ".archon", "factory.yaml"));
  assertHoldoutsOutsideRoot(root, factory);
  const config = applyArchonConfig(root, factory);
  const policies = applyPolicies(root, factory.autonomy);
  const triggers = applyTriggers(root, factory);
  return { config, policies, triggers, autonomy: factory.autonomy };
}

if (import.meta.main) {
  const command = process.argv[2] ?? "show";
  if (command === "show") console.log(JSON.stringify(readFactoryConfig(), null, 2));
  else if (command === "apply") console.log(JSON.stringify(applyFactoryConfig(), null, 2));
  else throw new Error("Usage: bun .archon/scripts/factory-config.ts [show|apply]");
}
