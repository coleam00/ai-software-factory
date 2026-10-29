import { createHash } from "node:crypto";

export interface CommitStatus { context: string; state: string; description?: string | null; created_at?: string }
export const PENDING_MAX_AGE_MS = 2 * 60 * 60 * 1000;

export function appCheckBinding(base: string, baseOid: string): string {
  return createHash("sha256").update(`${base}\0${baseOid}`).digest("hex");
}

export function boundDescription(base: string, baseOid: string, message: string): string {
  return `binding=${appCheckBinding(base, baseOid)}; ${message}`;
}

export function statusCoversBinding(statuses: CommitStatus[], base: string, baseOid: string, now = Date.now()): boolean {
  const latest = statuses.find(status => status.context === "factory/app-check");
  if (!latest || !["pending", "success", "failure", "error"].includes(latest.state)) return false;
  if (!(latest.description?.startsWith(`binding=${appCheckBinding(base, baseOid)};`) ?? false)) return false;
  if (latest.state !== "pending") return true;
  const created = Date.parse(latest.created_at ?? "");
  return Number.isFinite(created) && now - created <= PENDING_MAX_AGE_MS;
}
