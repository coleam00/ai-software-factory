import { emit, env, jsonEnv } from "../../.shared/utils.ts";

export interface HoldoutScope { run: string[]; skipped: string[]; over_budget: string[] }

/**
 * Turn the selector's choice into what the check must run. The selector is an agent, so its
 * answer is held to facts here: every id it names must exist, and every configured id ends up
 * either run or skipped, never silently gone. The budget is what keeps a check from taking hours:
 * the selector lists the most relevant first, and what is over budget is reported as skipped
 * for the rolling sweep to cover on the default branch.
 */
export function scopeHoldouts(all: string[], chosen: string[], max: number): HoldoutScope {
  const known = new Set(all);
  for (const id of chosen) {
    if (!known.has(id)) throw new Error(`the selector named a scenario that does not exist: ${id}`);
  }
  // An agent listing an id twice changes nothing about what must run, so it does not fail the check.
  const unique = [...new Set(chosen)];
  const run = unique.slice(0, max), over_budget = unique.slice(max);
  return { run, skipped: all.filter(id => !run.includes(id)), over_budget };
}

if (import.meta.main) {
  const config = jsonEnv<{ holdouts: { max_per_check: number } }>("config", { holdouts: { max_per_check: 8 } });
  emit(scopeHoldouts(jsonEnv<string[]>("ids", []), jsonEnv<string[] | null>("chosen", null) ?? [], Number(env("max", String(config.holdouts.max_per_check)))));
}
