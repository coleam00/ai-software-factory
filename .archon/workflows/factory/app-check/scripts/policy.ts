import type { FactoryConfig } from "../../../../scripts/factory-config.ts";
import { emit, env, jsonEnv, matchingFiles, postStatus } from "../../.shared/utils.ts";

interface ProtectedGrant { head: string; paths: string[] }
export function protectedGrantAllows(raw: string, head: string, paths: string[]): boolean {
  if (!raw || paths.length === 0) return false;
  let grant: ProtectedGrant;
  try { grant = JSON.parse(raw) as ProtectedGrant; } catch { return false; }
  if (!grant || typeof grant !== "object" || grant.head !== head || !Array.isArray(grant.paths) || grant.paths.some(path => typeof path !== "string")) return false;
  if (Object.keys(grant).sort().join(",") !== "head,paths") return false;
  const requested = [...new Set(paths)].sort(), granted = [...new Set(grant.paths)].sort();
  return grant.paths.length === granted.length && requested.length === granted.length && requested.every((path, index) => path === granted[index]);
}

if (import.meta.main) {
  const config = jsonEnv<FactoryConfig>("config", {} as FactoryConfig), files = jsonEnv<string[]>("files", []);
  const protectedFiles = matchingFiles(files, config.protected_paths);
  const ok = protectedFiles.length === 0 || protectedGrantAllows(env("grant"), env("head"), protectedFiles);
  const description = ok ? "policy clear" : "protected path: needs an owner ruling";
  postStatus(env("repo_path"), env("head"), ok ? "success" : "failure", "factory/policy", description);
  emit({ ok, protected_files: protectedFiles, description });
}
