import { existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { env, jsonEnv, run } from "../../.shared/utils.ts";
import { appProcessRecordPath, stopRecordedApp } from "./app-process.ts";

const tempFile = process.env.ARTIFACTS_DIR ? join(process.env.ARTIFACTS_DIR, "app-check", "temp-path.txt") : "";
const temp = env("temp") || (tempFile && existsSync(tempFile) ? readFileSync(tempFile, "utf8").trim() : "");
const appRecord = process.env.ARTIFACTS_DIR ? appProcessRecordPath() : "";
const appCleanup = appRecord ? stopRecordedApp(appRecord) : { status: "no-record" as const };
// A runtime-mode app is stopped by the owner's teardown, run from the checkout that started it.
const teardown = (jsonEnv<{ app?: { teardown?: string } }>("config", {}).app?.teardown ?? "").trim();
const teardownStatus = teardown && temp && existsSync(temp)
  ? (spawnSync("bash", ["-c", teardown], { cwd: temp, stdio: "ignore", timeout: 120_000 }).status === 0 ? "ok" : "failed")
  : "none";
if (temp) {
  run(["git", "worktree", "remove", "--force", temp], { allowFailure: true });
  const safePrefix = join(tmpdir(), "factory-app-check-").toLowerCase();
  if (existsSync(temp) && temp.toLowerCase().startsWith(safePrefix)) rmSync(temp, { recursive: true, force: true });
}
console.log(JSON.stringify({ cleaned: Boolean(temp), app_record: Boolean(appRecord && existsSync(appRecord)), app_cleanup: appCleanup.status, teardown: teardownStatus }));
