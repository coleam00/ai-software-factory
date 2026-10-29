import { existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { env, run } from "../../.shared/utils.ts";

const tempFile = process.env.ARTIFACTS_DIR ? join(process.env.ARTIFACTS_DIR, "app-check", "temp-path.txt") : "";
const temp = env("temp") || (tempFile && existsSync(tempFile) ? readFileSync(tempFile, "utf8").trim() : "");
const pidFile = process.env.ARTIFACTS_DIR ? join(process.env.ARTIFACTS_DIR, "app-check", "app.pid") : "";
if (pidFile && existsSync(pidFile)) {
  const pid = Number(readFileSync(pidFile, "utf8").trim());
  if (Number.isInteger(pid) && pid > 0) try { process.kill(pid); } catch {}
}
if (temp) {
  run(["git", "worktree", "remove", "--force", temp], { allowFailure: true });
  const safePrefix = join(tmpdir(), "factory-app-check-").toLowerCase();
  if (existsSync(temp) && temp.toLowerCase().startsWith(safePrefix)) rmSync(temp, { recursive: true, force: true });
}
console.log(JSON.stringify({ cleaned: Boolean(temp), pid_file: Boolean(pidFile && existsSync(pidFile)) }));
