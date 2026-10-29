import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const FORGE_ENV = /^(?:GH|GITHUB|GITLAB|GLAB|BITBUCKET)(?:_|$)/i;
const CREDENTIAL_ENV = /^(?:GIT_ASKPASS|SSH_ASKPASS|GCM_INTERACTIVE)$/i;

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function installFakeCommand(directory: string, name: string, source: string): string {
  const script = join(directory, `fake-${name}.ts`);
  writeFileSync(script, source);

  if (process.platform === "win32") {
    const command = join(directory, `${name}.exe`);
    const built = Bun.spawnSync([process.execPath, "build", "--compile", script, "--outfile", command], {
      stdout: "pipe",
      stderr: "pipe",
    });
    if (built.exitCode !== 0) {
      throw new Error(`failed to compile fake ${name}: ${built.stderr.toString().trim()}`);
    }
    return command;
  }

  const command = join(directory, name);
  writeFileSync(command, `#!/bin/sh\nexec ${shellQuote(process.execPath)} ${shellQuote(script)} "$@"\n`);
  chmodSync(command, 0o755);
  return command;
}

export function isolatedCommandEnv(
  directory: string,
  overrides: Record<string, string> = {},
): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value === undefined || key.toUpperCase() === "PATH" || FORGE_ENV.test(key) || CREDENTIAL_ENV.test(key)) continue;
    environment[key] = value;
  }

  const config = join(directory, "forge-config");
  mkdirSync(config, { recursive: true });
  environment.PATH = directory;
  environment.GH_CONFIG_DIR = config;
  environment.GH_PROMPT_DISABLED = "1";
  environment.GIT_TERMINAL_PROMPT = "0";

  for (const [key, value] of Object.entries(overrides)) {
    for (const existing of Object.keys(environment)) {
      if (existing.toUpperCase() === key.toUpperCase()) delete environment[existing];
    }
    environment[key] = value;
  }
  return environment;
}
