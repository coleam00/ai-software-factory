import { emit, env, ghJson, linkedIssue } from "../../.shared/utils.ts";
import { currentBranchOid } from "./base.ts";
import { appCheckBinding, statusCoversBinding, type CommitStatus } from "./binding.ts";

export interface Candidate { number: number; isDraft: boolean; createdAt: string; headRefOid: string; baseRefName: string; baseRefOid: string; files: { path: string }[]; body: string; state?: string }
type PullCandidate = Omit<Candidate, "baseRefOid"> & { baseRefOid?: string };
export function candidateBinding(row: Candidate): string {
  return `${row.headRefOid}:${appCheckBinding(row.baseRefName, row.baseRefOid)}`;
}
export function oldestUnchecked(rows: Candidate[], coveredBindings: Set<string>): Candidate | null {
  return rows.filter(row => (!row.state || row.state === "OPEN") && !coveredBindings.has(candidateBinding(row))).sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0] ?? null;
}

function hasCurrentStatus(repo: string, row: Candidate): boolean {
  const statuses = ghJson(["api", `repos/${repo}/commits/${row.headRefOid}/statuses`]) as CommitStatus[];
  return statusCoversBinding(statuses, row.baseRefName, row.baseRefOid);
}

if (import.meta.main) {
  const repo = env("repo_path"), requested = env("pr");
  const fields = "number,isDraft,createdAt,headRefOid,baseRefName,baseRefOid,files,body,state";
  const pullRows = requested
    ? [ghJson(["pr", "view", requested, "--repo", repo, "--json", fields]) as PullCandidate]
    : ghJson(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json", fields]) as PullCandidate[];
  const baseTips = new Map<string, string>();
  const rows = pullRows.map(row => {
    const baseRefOid = baseTips.get(row.baseRefName) ?? currentBranchOid(repo, row.baseRefName);
    baseTips.set(row.baseRefName, baseRefOid);
    return { ...row, baseRefOid } as Candidate;
  });
  const covered = new Set(requested ? [] : rows.filter(row => hasCurrentStatus(repo, row)).map(candidateBinding));
  const picked = requested ? (rows[0]?.state === "OPEN" ? rows[0] : null) : oldestUnchecked(rows, covered);
  if (!picked) emit({ found: false, pr: null, head: "", files: [], base: "", base_oid: "", issue: null, issue_body: "", issue_labels: [] });
  else {
    const issue = linkedIssue(picked.body);
    const detail = issue ? ghJson(["issue", "view", String(issue), "--repo", repo, "--json", "body,labels"]) : null;
    emit({ found: true, pr: picked.number, head: picked.headRefOid, files: picked.files.map(file => file.path), base: picked.baseRefName, base_oid: picked.baseRefOid, issue,
      issue_body: detail?.body ?? "", issue_labels: (detail?.labels ?? []).map((label: { name: string }) => label.name) });
  }
}
