import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { boundDescription } from "./binding.ts";
import { emit, postStatus } from "../../.shared/utils.ts";

interface PendingRecord { repo: string; head: string; base: string; base_oid: string }
type StatusWriter = (repo: string, head: string, state: "pending" | "success" | "failure", context: string, description: string) => void;

export function finalizePending(root: string, writeStatus: StatusWriter = postStatus): boolean {
  const pendingPath = join(root, "pending.json"), terminalPath = join(root, "terminal.json");
  if (existsSync(terminalPath) || !existsSync(pendingPath)) return false;
  const pending = JSON.parse(readFileSync(pendingPath, "utf8")) as PendingRecord;
  writeStatus(pending.repo, pending.head, "failure", "factory/app-check", boundDescription(pending.base, pending.base_oid, "app check did not complete"));
  mkdirSync(root, { recursive: true });
  writeFileSync(terminalPath, JSON.stringify({ state: "failure", reason: "incomplete" }));
  return true;
}

if (import.meta.main) {
  emit({ recovered: finalizePending(join(process.env.ARTIFACTS_DIR!, "app-check")) });
}
