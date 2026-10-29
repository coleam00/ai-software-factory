import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import type { FactoryConfig } from "../../../../scripts/factory-config.ts";
import { emit, env, jsonEnv } from "../../.shared/utils.ts";

export function configuredHoldoutIds(names: string[]): string[] {
  const scenarioNames = names.filter(name => /^H-.*\.md$/i.test(name));
  const ids = scenarioNames.map(name => name.match(/^(H-[A-Za-z0-9_]+)(?:-[^/]+)?\.md$/)?.[1]);
  if (ids.some(id => !id)) throw new Error("holdout filenames must be H-<id>-<slug>.md");
  const unique = new Set(ids as string[]);
  if (unique.size !== ids.length) throw new Error("holdout scenario ids must be unique");
  return [...unique].sort();
}

if (import.meta.main) {
  const config = jsonEnv<FactoryConfig>("config", {} as FactoryConfig);
  const depth = env("depth", "light") as "light" | "normal" | "deep";
  if (!config.holdouts.enabled || depth === "light") emit({ ids: [] });
  else {
    const configured = config.holdouts.path.replace(/^~(?=[/\\]|$)/, homedir());
    const dir = resolve(configured);
    if (!existsSync(dir)) throw new Error("configured holdout directory does not exist");
    const ids = configuredHoldoutIds(readdirSync(dir, { withFileTypes: true }).filter(entry => entry.isFile()).map(entry => entry.name));
    if (!ids.length) throw new Error("configured holdout directory has no scenarios");
    emit({ ids });
  }
}
