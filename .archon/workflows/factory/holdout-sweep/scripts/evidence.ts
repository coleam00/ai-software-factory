import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { evidenceProblems } from "../../app-check/scripts/evidence.ts";
import { emit, env, jsonEnv, readJsonFile } from "../../.shared/utils.ts";

const root = join(process.env.ARTIFACTS_DIR!, "app-check"), resultPath = join(root, "result.json"), reportPath = join(root, "report.md");
if (!existsSync(resultPath) || !existsSync(reportPath)) throw new Error("check must write app-check/result.json and app-check/report.md");
const preparedAt = Number(env("prepared_at"));
if (statSync(resultPath).mtimeMs < preparedAt || statSync(reportPath).mtimeMs < preparedAt) throw new Error("result.json and report.md must be written after prepare");
const result = readJsonFile<{ overall: string; lines: string[] }>(resultPath);
const problems = evidenceProblems(result, readFileSync(reportPath, "utf8"), root, preparedAt, jsonEnv<string[]>("expected_holdouts", []), { requireCriteria: false });
if (problems.length) throw new Error(problems.join("\n"));
emit({ overall: result.overall, lines: result.lines });
