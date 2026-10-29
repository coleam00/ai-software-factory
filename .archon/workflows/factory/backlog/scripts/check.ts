import { readFileSync } from "node:fs";
import { emit } from "../../.shared/utils.ts";

const REQUIRED = ["Problem", "Why", "Why now", "Outcome", "Invariants", "Acceptance"];
export function validateBacklog(value: unknown): string[] {
  const errors: string[] = [];
  if (!Array.isArray(value)) return ["backlog.json must be an array"];
  if (value.length > 12) errors.push("backlog exceeds 12 tickets");
  const seen = new Set<string>();
  value.forEach((raw, index) => {
    const ticket = raw as any, key = ticket?.key;
    if (typeof key !== "string" || !/^[a-z0-9][a-z0-9-]{1,60}$/.test(key)) errors.push(`ticket ${index + 1} has an invalid key`);
    else if (seen.has(key)) errors.push(`duplicate key ${key}`);
    if (typeof ticket?.title !== "string" || !ticket.title.trim()) errors.push(`${key ?? index + 1} needs a title`);
    if (typeof ticket?.body !== "string") errors.push(`${key ?? index + 1} needs a body`);
    else {
      for (const heading of REQUIRED) if (!new RegExp(`^## ${heading}\\s*$`, "mi").test(ticket.body)) errors.push(`${key} lacks ${heading}`);
      if (!ticket.body.includes(`<!-- factory-backlog: ${key} -->`)) errors.push(`${key} lacks its dedup marker`);
      if (!/^\s*- \[[ xX]\]/m.test(ticket.body.split(/^## Acceptance\s*$/mi)[1] ?? "")) errors.push(`${key} acceptance is not a checklist`);
    }
    const deps = ticket?.depends_on;
    if (!Array.isArray(deps) || deps.some((dep: unknown) => typeof dep !== "string" || !seen.has(dep))) errors.push(`${key} dependencies must name earlier tickets`);
    if (index === 0 && deps?.length) errors.push("first ticket must have no dependencies");
    if (!["s", "m", "l"].includes(ticket?.size)) errors.push(`${key} size must be s, m, or l`);
    if (typeof key === "string") seen.add(key);
  });
  return errors;
}

if (import.meta.main) {
  const tickets = JSON.parse(readFileSync(`${process.env.ARTIFACTS_DIR}/backlog.json`, "utf8"));
  const errors = validateBacklog(tickets); if (errors.length) throw new Error(errors.join("\n"));
  emit({ count: tickets.length, keys: tickets.map((ticket: any) => ticket.key) });
}
