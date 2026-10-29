import { ghJson } from "../../.shared/utils.ts";

interface GitRef { object?: { sha?: unknown } }

export function currentBranchOid(repo: string, branch: string): string {
  const ref = ghJson(["api", `repos/${repo}/git/ref/heads/${encodeURIComponent(branch)}`]) as GitRef;
  const sha = ref?.object?.sha;
  if (typeof sha !== "string" || !sha) throw new Error(`GitHub returned no commit for branch ${branch}`);
  return sha;
}
