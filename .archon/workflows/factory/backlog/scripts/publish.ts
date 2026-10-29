import { readFileSync } from "node:fs";
import { emit, env, ghJson, run } from "../../.shared/utils.ts";

const repo = env("repo_path"), tickets = JSON.parse(readFileSync(`${process.env.ARTIFACTS_DIR}/backlog.json`, "utf8"));
const listed = ghJson(["issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number,url,body"]) as any[];
const created: string[] = [], existing: string[] = [], numbers = new Map<string, number>();
for (const ticket of tickets) {
  const marker = `<!-- factory-backlog: ${ticket.key} -->`;
  const prior = listed.find(issue => issue.body?.includes(marker));
  if (prior) { existing.push(prior.url); numbers.set(ticket.key, prior.number); continue; }
  const dependencies = ticket.depends_on.map((key: string) => `Depends on: #${numbers.get(key)}`).join("\n");
  const body = ticket.body.replace(marker, "").trim() + (dependencies ? `\n\n${dependencies}` : "") + `\n\n${marker}\n`;
  const file = `${process.env.ARTIFACTS_DIR}/issue-${ticket.key}.md`; await Bun.write(file, body);
  const url = run(["gh", "issue", "create", "--repo", repo, "--title", ticket.title, "--body-file", file]);
  const number = Number(url.match(/\/(\d+)$/)?.[1]);
  if (!number) throw new Error(`could not read created issue URL: ${url}`);
  created.push(url); numbers.set(ticket.key, number); listed.push({ url, number, body });
}
emit({ published: true, created, existing });
