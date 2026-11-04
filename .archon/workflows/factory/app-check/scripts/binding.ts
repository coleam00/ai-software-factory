import { createHash } from "node:crypto";

export interface CommitStatus { context: string; state: string; description?: string | null; created_at?: string }
export const PENDING_MAX_AGE_MS = 2 * 60 * 60 * 1000;
const NOT_ABOUT_THE_CODE = /did not complete|stale:/;

export function appCheckBinding(base: string, baseOid: string): string {
  return createHash("sha256").update(`${base}\0${baseOid}`).digest("hex");
}

export function boundDescription(base: string, baseOid: string, message: string): string {
  return `binding=${appCheckBinding(base, baseOid)}; ${message}`;
}

/**
 * A result belongs to the pull request's commit. A pass or a failure stands until a new commit
 * arrives, so a merge to the base no longer voids every open check. Results the check itself says
 * are not about the code (it never finished) do not count, so the scheduler runs them again.
 */
export function statusJudgesHead(statuses: CommitStatus[], now = Date.now()): boolean {
  const latest = statuses.find(status => status.context === "factory/app-check");
  if (!latest) return false;
  if (latest.state === "success") return true;
  if (latest.state === "failure") return !NOT_ABOUT_THE_CODE.test(latest.description ?? "");
  if (latest.state !== "pending") return false;
  const created = Date.parse(latest.created_at ?? "");
  return Number.isFinite(created) && now - created <= PENDING_MAX_AGE_MS;
}
