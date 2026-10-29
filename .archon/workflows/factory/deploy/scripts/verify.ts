import { existsSync, statSync } from "node:fs";
import { basename } from "node:path";
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

if (import.meta.main) console.log(JSON.stringify(verifyDeployment(
  env("requested_revision"), env("reported_requested_revision"), env("deployed_revision"), env("health_status"),
  jsonEnv<string[]>("evidence_files", []), Number(env("started_at")),
)));
