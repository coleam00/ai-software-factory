import { existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { env, jsonEnv } from "../../.shared/utils.ts";

export function verifyDeployment(requested: string, reported: string, deployed: string, health: string, evidence: string[], startedAt: number) {
  requested = requested.toLowerCase(); reported = reported.toLowerCase(); deployed = deployed.toLowerCase();
  if (!requested || reported !== requested) throw new Error("deployment report did not bind to the requested revision");
  if (deployed !== requested) throw new Error(`deployed revision mismatch: requested ${requested}, observed ${deployed || "nothing"}`);
  if (health !== "healthy") throw new Error(`deployment health check was ${health || "not reported"}`);
  if (!evidence.length) throw new Error("deployment returned no evidence files");
  if (!evidence.some(file => basename(file).toLowerCase() === "report.md")) throw new Error("deployment evidence must include report.md");
  for (const file of evidence) {
    if (!existsSync(file) || !statSync(file).isFile()) throw new Error(`deployment evidence is missing: ${file}`);
    if (!Number.isFinite(startedAt) || statSync(file).mtimeMs < startedAt) throw new Error(`deployment evidence is stale: ${file}`);
  }
  return { deployed: true, revision: deployed, health, evidence_files: evidence };
}

/** A failed deployment has no open pull request to return to, so it is recorded for the discoveries workflow to file as new work. */
export function recordFailedDeployment(artifacts: string, requested: string, reason: string, evidence: string[]): void {
  const dir = join(artifacts, "discoveries");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "deploy.json"), JSON.stringify([{
    title: `Deploying ${requested.slice(0, 7) || "the requested revision"} failed`,
    claim: reason,
    evidence: [`requested revision: ${requested || "none"}`, ...evidence],
    relation: "unrelated",
    source_node: "deploy",
  }], null, 2));
}

if (import.meta.main) {
  const evidence = jsonEnv<string[]>("evidence_files", []);
  try {
    console.log(JSON.stringify(verifyDeployment(
      env("requested_revision"), env("reported_requested_revision"), env("deployed_revision"), env("health_status"),
      evidence, Number(env("started_at")),
    )));
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    if (process.env.ARTIFACTS_DIR) recordFailedDeployment(process.env.ARTIFACTS_DIR, env("requested_revision"), reason, evidence);
    throw error;
  }
}
