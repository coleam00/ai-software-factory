import { existsSync, readdirSync } from "node:fs";
import { configuredHoldoutIds } from "../../app-check/scripts/holdouts.ts";
import { holdoutDir, readLedger, type Ledger } from "../../app-check/scripts/ledger.ts";
import { emit, env, ghJson, jsonEnv } from "../../.shared/utils.ts";

/** The scenarios not yet checked at this commit, checked longest ago first, never-checked before all. */
export function pickBatch(ids: string[], ledger: Ledger, head: string, size: number): string[] {
  const at = (id: string) => ledger.scenarios[id]?.at ?? "";
  return ids
    .filter(id => ledger.scenarios[id]?.commit !== head)
    .sort((a, b) => at(a).localeCompare(at(b)) || a.localeCompare(b))
    .slice(0, size);
}

if (import.meta.main) {
  const config = jsonEnv<{ holdouts: { enabled: boolean; path: string; sweep_size: number } }>("config", { holdouts: { enabled: false, path: "", sweep_size: 0 } });
  const none = (reason: string, base = "", head = "", dir = "") => emit({ run: false, ids: [], base, head, holdout_dir: dir, reason });
  if (!config.holdouts.enabled) none("holdouts are disabled");
  else {
    const dir = holdoutDir(config.holdouts.path);
    if (!existsSync(dir)) throw new Error("configured holdout directory does not exist");
    const repo = ghJson(["repo", "view", env("repo_path"), "--json", "defaultBranchRef"]) as { defaultBranchRef: { name: string; target: { oid: string } } };
    const base = repo.defaultBranchRef.name, head = repo.defaultBranchRef.target.oid;
    const ids = configuredHoldoutIds(readdirSync(dir, { withFileTypes: true }).filter(entry => entry.isFile()).map(entry => entry.name));
    const batch = pickBatch(ids, readLedger(config.holdouts.path), head, config.holdouts.sweep_size);
    if (!batch.length) none("every scenario is checked at the current commit", base, head, dir);
    else emit({ run: true, ids: batch, base, head, holdout_dir: dir, reason: `${batch.length} of ${ids.length} scenarios not yet checked at this commit` });
  }
}
