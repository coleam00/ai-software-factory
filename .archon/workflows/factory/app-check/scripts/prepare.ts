import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { emit, env, run } from "../../.shared/utils.ts";

const base = env("base"), baseOid = env("base_oid"), head = env("head"), pr = env("pr");
const temp = mkdtempSync(join(tmpdir(), "factory-app-check-"));
const artifactRoot = join(process.env.ARTIFACTS_DIR!, "app-check");
mkdirSync(artifactRoot, { recursive: true });
writeFileSync(join(artifactRoot, "temp-path.txt"), temp);
const headRef = `refs/factory/app-check-${pr}`;
run(["git", "fetch", "origin", `+refs/heads/${base}:refs/remotes/origin/${base}`, `+pull/${pr}/head:${headRef}`]);
const fetchedBase = run(["git", "rev-parse", `refs/remotes/origin/${base}`]);
const fetchedHead = run(["git", "rev-parse", headRef]);
if (fetchedBase !== baseOid || fetchedHead !== head) throw new Error("pull request head/base changed before preparation");
run(["git", "worktree", "add", "--detach", temp, fetchedBase]);
run(["git", "merge", "--no-edit", fetchedHead], { cwd: temp });
emit({ temp, merged: run(["git", "rev-parse", "HEAD"], { cwd: temp }), prepared_at: Date.now() });
