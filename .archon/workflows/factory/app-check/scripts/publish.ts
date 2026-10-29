import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { currentBranchOid } from "./base.ts";
import { boundDescription } from "./binding.ts";
import { env, ghJson, jsonEnv, postStatus, run } from "../../.shared/utils.ts";

interface CurrentPull { state: string; headRefOid: string; baseRefName: string }
export function bindingIsCurrent(current: CurrentPull, currentBaseOid: string, head: string, base: string, baseOid: string): boolean {
  return current.state === "OPEN" && current.headRefOid === head && current.baseRefName === base && currentBaseOid === baseOid;
}

if (import.meta.main) {
  const repo = env("repo_path"), head = env("head"), base = env("base"), baseOid = env("base_oid"), pr = env("pr"), overall = env("overall");
  const lines = jsonEnv<string[]>("lines", []);
  const current = ghJson(["pr", "view", pr, "--repo", repo, "--json", "state,headRefOid,baseRefName"]) as CurrentPull;
  const fresh = bindingIsCurrent(current, currentBranchOid(repo, current.baseRefName), head, base, baseOid);
  const state = overall === "PASS" && fresh ? "success" : "failure";
  const message = !fresh ? "stale: pull request head/base changed" : overall === "PASS" ? "black-box app check passed" : "black-box app check failed";
  postStatus(repo, head, state, "factory/app-check", boundDescription(base, baseOid, message));
  const marker = "<!-- factory-app-check -->";
  const freshness = `Tested head \`${head}\` against \`${base}\` at \`${baseOid}\`.${fresh ? "" : " The pull request changed before publication; this result is a failure."}`;
  const body = `${marker}\n## Factory app check: ${state === "success" ? "PASS" : "FAIL"}\n\n${freshness}\n\n${lines.join("\n")}`;
  const comments = ghJson(["api", `repos/${repo}/issues/${pr}/comments`, "--paginate"]) as { id: number; body: string }[];
  const prior = comments.find(comment => comment.body.includes(marker));
  const root = join(process.env.ARTIFACTS_DIR!, "app-check"), file = join(root, "comment.json");
  await Bun.write(file, JSON.stringify({ body }));
  if (prior) run(["gh", "api", "--method", "PATCH", `repos/${repo}/issues/comments/${prior.id}`, "--input", file]);
  else run(["gh", "api", "--method", "POST", `repos/${repo}/issues/${pr}/comments`, "--input", file]);
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "terminal.json"), JSON.stringify({ state, fresh }));
  console.log(JSON.stringify({ state, comment: prior ? "updated" : "created" }));
}
