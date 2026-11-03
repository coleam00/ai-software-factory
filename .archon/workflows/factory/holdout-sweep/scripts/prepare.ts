import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { emit, env, run } from "../../.shared/utils.ts";

const base = env("base"), head = env("head");
const temp = mkdtempSync(join(tmpdir(), "factory-app-check-"));
const artifactRoot = join(process.env.ARTIFACTS_DIR!, "app-check");
mkdirSync(artifactRoot, { recursive: true });
writeFileSync(join(artifactRoot, "temp-path.txt"), temp);
run(["git", "fetch", "origin", `+refs/heads/${base}:refs/remotes/origin/${base}`]);
run(["git", "worktree", "add", "--detach", temp, head]);
emit({ temp, merged: head, prepared_at: Date.now() });
