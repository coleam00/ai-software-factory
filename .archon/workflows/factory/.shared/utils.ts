export function globRegex(pattern: string): RegExp {
  let source = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "*" && pattern[i + 1] === "*") {
      const slash = pattern[i + 2] === "/";
      source += slash ? "(?:.*/)?" : ".*";
      i += slash ? 2 : 1;
    } else if (c === "*") source += "[^/]*";
    else if (c === "?") source += "[^/]";
    else source += c!.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${source}$`);
}

export const matchingFiles = (files: string[], patterns: string[]) => {
  const regexes = patterns.map(globRegex);
  return files.filter(file => regexes.some(regex => regex.test(file.replace(/\\/g, "/").replace(/^\.\//, ""))));
};

export function env(name: string, fallback = ""): string { return process.env[`INPUTS_${name.toUpperCase()}`]?.trim() || fallback; }
export function jsonEnv<T>(name: string, fallback: T): T { const value = env(name); return value ? JSON.parse(value) as T : fallback; }
export function emit(value: unknown): void { console.log(JSON.stringify(value)); }

export function run(command: string[], options: { cwd?: string; allowFailure?: boolean } = {}): string {
  const result = Bun.spawnSync(command, { cwd: options.cwd, stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0 && !options.allowFailure) throw new Error(`${command.slice(0, 3).join(" ")} failed: ${result.stderr.toString().trim()}`);
  return result.stdout.toString().trim();
}

export function ghJson(args: string[]): any { const text = run(["gh", ...args]); return text ? JSON.parse(text) : null; }
export function postStatus(repo: string, head: string, state: "pending" | "success" | "failure", context: string, description: string): void {
  run(["gh", "api", "--method", "POST", `repos/${repo}/statuses/${head}`, "-f", `state=${state}`, "-f", `context=${context}`, "-f", `description=${description.slice(0, 140)}`]);
}

export function linkedIssue(body: string): number | null {
  const match = body.match(/(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+(?:[\w.-]+\/[\w.-]+)?#(\d+)/i);
  return match ? Number(match[1]) : null;
}
