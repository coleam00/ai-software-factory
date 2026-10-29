import { existsSync } from "node:fs";
import { resolve } from "node:path";

const revision = process.env.INPUTS_REVISION?.trim() ?? "";
if (!/^[0-9a-f]{40}$/i.test(revision)) throw new Error("revision must be an exact 40-character commit id");
const deployDoc = resolve("DEPLOY.md");
if (!existsSync(deployDoc)) throw new Error("DEPLOY.md is required; factory-deploy never invents deployment instructions");
console.log(JSON.stringify({ revision: revision.toLowerCase(), deploy_doc: deployDoc, started_at: Date.now() }));
