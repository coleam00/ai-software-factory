// Installs, lists, and removes the OS timers that keep this factory running.
//
// Archon installs schedules itself only on macOS (`archon trigger schedule install`).
// On Windows and Linux its docs say to point the host scheduler at
// `archon trigger fire --config <binding>`. This script does exactly that for every
// binding factory-setup wrote to .archon/triggers/, plus one timer for the intake poll
// (.archon/scripts/intake-poll.ts), which hands new issues to Archon without a webhook.
// It decides nothing about the work; Archon's bindings admit, queue, and skip.
//
// Usage: bun .archon/scripts/factory-schedule.ts <install|remove|status> [project-root]

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join, resolve } from "node:path";
import { readFactoryConfig } from "./factory-config.ts";

export interface Timer {
  /** Short stable name, e.g. "intake" or "build-queue". */
  name: string;
  intervalSeconds: number;
  /** Absolute program plus arguments, run from the project root. */
  command: string[];
}

function which(command: string): string {
  const found = Bun.which(command);
  if (!found) throw new Error(`${command} is not on PATH; install it first`);
  return found;
}

/** Every timer this project needs, from the bindings factory-setup wrote. */
export function timersFor(root: string, tools = { archon: "archon", bun: process.execPath }): Timer[] {
  const directory = join(root, ".archon", "triggers");
  if (!existsSync(directory)) return [];
  const timers: Timer[] = [];
  for (const file of readdirSync(directory).filter(name => name.endsWith(".json")).sort()) {
    const path = join(directory, file);
    const binding = JSON.parse(readFileSync(path, "utf8"));
    const intervalSeconds = Number(binding?.schedule?.intervalSeconds ?? 0);
    if (!intervalSeconds) continue;
    const name = basename(file, ".json");
    timers.push(name === "intake"
      ? { name, intervalSeconds, command: [tools.bun, join(root, ".archon", "scripts", "intake-poll.ts"), root] }
      : { name, intervalSeconds, command: [tools.archon, "trigger", "fire", "--config", path] });
  }
  return timers;
}

/** One id per project checkout, so two factories on one machine never share timers. */
export function projectId(root: string, repoPath: string): string {
  const slug = repoPath.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase() || "factory";
  const hash = createHash("sha256").update(resolve(root).toLowerCase()).digest("hex").slice(0, 6);
  return `${slug}-${hash}`;
}

const stateDir = (id: string) => join(homedir(), ".archon", "factory-timers", id);

// ---------------------------------------------------------------- Windows

const xmlEscape = (value: string) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const vbsString = (value: string) => `"${value.replace(/"/g, '""')}"`;
const cmdQuote = (value: string) => (/[\s"&|<>^]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);

/** A VBScript launcher, so the task runs with no console window popping up. */
export function windowsLauncher(root: string, command: string[], log: string, env: Record<string, string>): string {
  const line = `cmd /c "${command.map(cmdQuote).join(" ")} >> ${cmdQuote(log)} 2>&1"`;
  return [
    'Set shell = CreateObject("WScript.Shell")',
    `shell.CurrentDirectory = ${vbsString(root)}`,
    ...Object.entries(env).map(([key, value]) => `shell.Environment("Process")(${vbsString(key)}) = ${vbsString(value)}`),
    `WScript.Quit shell.Run(${vbsString(line)}, 0, True)`,
    "",
  ].join("\r\n");
}

/** Task Scheduler XML: repeat forever, run when available, never overlap, run on battery. */
export function windowsTaskXml(launcher: string, intervalSeconds: number, start = new Date()): string {
  const minutes = Math.max(1, Math.ceil(intervalSeconds / 60));
  // Keep the Z: without it Task Scheduler reads a UTC time as local time.
  const boundary = new Date(start.getTime() + 60_000).toISOString().replace(/\.\d+Z$/, "Z");
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AI Software Factory timer (factory-schedule)</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT${minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>${boundary}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>wscript.exe</Command><Arguments>${xmlEscape(`"${launcher}"`)}</Arguments></Exec>
  </Actions>
</Task>
`;
}

const taskName = (id: string, timer: string) => `\\ArchonFactory\\${id}\\${timer}`;

function run(command: string[]): { ok: boolean; out: string } {
  const result = Bun.spawnSync(command, { stdout: "pipe", stderr: "pipe" });
  return { ok: result.exitCode === 0, out: `${result.stdout}${result.stderr}`.trim() };
}

function installWindows(root: string, id: string, timers: Timer[]): string[] {
  const dir = stateDir(id);
  mkdirSync(dir, { recursive: true });
  const env: Record<string, string> = { ARCHON_BIN: which("archon"), GH_BIN: which("gh") };
  const done: string[] = [];
  for (const timer of timers) {
    const log = join(dir, `${timer.name}.log`);
    const launcher = join(dir, `${timer.name}.vbs`);
    writeFileSync(launcher, windowsLauncher(root, timer.command, log, env));
    const xml = join(dir, `${timer.name}.xml`);
    writeFileSync(xml, Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(windowsTaskXml(launcher, timer.intervalSeconds), "utf16le")]));
    const result = run(["schtasks", "/Create", "/F", "/TN", taskName(id, timer.name), "/XML", xml]);
    if (!result.ok) throw new Error(`Task Scheduler refused ${timer.name}: ${result.out}`);
    done.push(`${taskName(id, timer.name)} every ${Math.ceil(timer.intervalSeconds / 60)} min, log ${log}`);
  }
  return done;
}

function removeWindows(id: string, timers: string[]): string[] {
  return timers.filter(name => run(["schtasks", "/Delete", "/F", "/TN", taskName(id, name)]).ok).map(name => taskName(id, name));
}

function statusWindows(id: string, timers: string[]): { name: string; installed: boolean; detail: string }[] {
  return timers.map(name => {
    const result = run(["schtasks", "/Query", "/TN", taskName(id, name), "/V", "/FO", "LIST"]);
    const pick = (label: string) => result.out.match(new RegExp(`^${label}:\\s*(.*)$`, "m"))?.[1]?.trim() ?? "";
    return { name, installed: result.ok, detail: result.ok ? `next ${pick("Next Run Time")}, last result ${pick("Last Result")}` : "not installed" };
  });
}

// ---------------------------------------------------------------- macOS

const plistLabel = (id: string, timer: string) => `ai.archon.factory.${id}.${timer}`;
const plistPath = (id: string, timer: string) => join(homedir(), "Library", "LaunchAgents", `${plistLabel(id, timer)}.plist`);
const uid = () => String(process.getuid?.() ?? "");

export function launchdPlist(label: string, root: string, command: string[], intervalSeconds: number, log: string, env: Record<string, string>): string {
  const esc = (value: string) => xmlEscape(value);
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${esc(label)}</string>
  <key>ProgramArguments</key><array>${command.map(part => `<string>${esc(part)}</string>`).join("")}</array>
  <key>WorkingDirectory</key><string>${esc(root)}</string>
  <key>EnvironmentVariables</key><dict>${Object.entries(env).map(([k, v]) => `<key>${esc(k)}</key><string>${esc(v)}</string>`).join("")}</dict>
  <key>StartInterval</key><integer>${intervalSeconds}</integer>
  <key>StandardOutPath</key><string>${esc(log)}</string>
  <key>StandardErrorPath</key><string>${esc(log)}</string>
</dict></plist>
`;
}

function installMac(root: string, id: string, timers: Timer[]): string[] {
  const dir = stateDir(id);
  mkdirSync(dir, { recursive: true });
  const archon = which("archon"), done: string[] = [];
  for (const timer of timers) {
    if (timer.name !== "intake") {
      // Archon's own installer for its bindings.
      const result = run([archon, "trigger", "schedule", "install", "--config", timer.command.at(-1)!]);
      if (!result.ok) throw new Error(`archon trigger schedule install failed for ${timer.name}: ${result.out}`);
      done.push(`${timer.name}: installed by Archon (launchd)`);
      continue;
    }
    const path = plistPath(id, timer.name), log = join(dir, `${timer.name}.log`);
    mkdirSync(join(homedir(), "Library", "LaunchAgents"), { recursive: true });
    writeFileSync(path, launchdPlist(plistLabel(id, timer.name), root, timer.command, timer.intervalSeconds, log,
      { ARCHON_BIN: archon, GH_BIN: which("gh"), PATH: process.env.PATH ?? "" }));
    run(["launchctl", "bootout", `gui/${uid()}`, path]);
    const result = run(["launchctl", "bootstrap", `gui/${uid()}`, path]);
    if (!result.ok) throw new Error(`launchctl bootstrap failed for ${timer.name}: ${result.out}`);
    done.push(`${plistLabel(id, timer.name)} every ${timer.intervalSeconds}s, log ${log}`);
  }
  return done;
}

// ---------------------------------------------------------------- Linux (cron)

const cronMarker = (id: string, timer: string) => `# archon-factory:${id}:${timer}`;

export function cronLine(id: string, root: string, timer: Timer, log: string): string {
  const minutes = Math.max(1, Math.ceil(timer.intervalSeconds / 60));
  const when = minutes < 60 ? `*/${minutes} * * * *` : `0 */${Math.max(1, Math.round(minutes / 60))} * * *`;
  const quote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;
  return `${when} cd ${quote(root)} && ${timer.command.map(quote).join(" ")} >> ${quote(log)} 2>&1 ${cronMarker(id, timer.name)}`;
}

function readCrontab(): string[] {
  const result = run(["crontab", "-l"]);
  return result.ok ? result.out.split("\n").filter(Boolean) : [];
}

function writeCrontab(lines: string[]): void {
  const file = join(stateDir("cron"), "crontab.txt");
  mkdirSync(stateDir("cron"), { recursive: true });
  writeFileSync(file, `${lines.join("\n")}\n`);
  const result = run(["crontab", file]);
  if (!result.ok) throw new Error(`crontab refused the update: ${result.out}`);
}

// ---------------------------------------------------------------- entry point

function main(): void {
  const [action = "status", rootArg = "."] = process.argv.slice(2);
  const root = resolve(rootArg);
  const config = readFactoryConfig(join(root, ".archon", "factory.yaml"));
  const id = projectId(root, config.project.repo_path);
  const known = ["intake", "build-queue", "merge-queue", "discoveries", "factory-manager", "factory-app-check"];
  const platform = process.platform;
  let result: unknown;

  if (action === "install") {
    if (!config.triggers.enabled) throw new Error("triggers.enabled is false in .archon/factory.yaml; enable it and run factory-setup first");
    const timers = timersFor(root, { archon: which("archon"), bun: process.execPath });
    if (!timers.length) throw new Error("No bindings in .archon/triggers/; run factory-setup first");
    if (platform === "win32") result = installWindows(root, id, timers);
    else if (platform === "darwin") result = installMac(root, id, timers);
    else {
      const kept = readCrontab().filter(line => !line.includes(`# archon-factory:${id}:`));
      mkdirSync(stateDir(id), { recursive: true });
      writeCrontab([...kept, ...timers.map(timer => cronLine(id, root, timer, join(stateDir(id), `${timer.name}.log`)))]);
      result = timers.map(timer => `${timer.name}: cron`);
    }
  } else if (action === "remove") {
    if (platform === "win32") result = removeWindows(id, known);
    else if (platform === "darwin") {
      const archon = which("archon");
      for (const timer of timersFor(root, { archon, bun: process.execPath }).filter(t => t.name !== "intake"))
        run([archon, "trigger", "schedule", "remove", "--config", timer.command.at(-1)!]);
      const path = plistPath(id, "intake");
      if (existsSync(path)) { run(["launchctl", "bootout", `gui/${uid()}`, path]); rmSync(path); }
      result = "removed";
    } else {
      writeCrontab(readCrontab().filter(line => !line.includes(`# archon-factory:${id}:`)));
      result = "removed";
    }
  } else if (action === "status") {
    if (platform === "win32") result = statusWindows(id, known).filter(entry => entry.installed || timersFor(root).some(t => t.name === entry.name));
    else if (platform === "darwin") result = { intake: existsSync(plistPath(id, "intake")) ? "installed" : "not installed", note: "queue timers: archon trigger list" };
    else result = readCrontab().filter(line => line.includes(`# archon-factory:${id}:`));
  } else {
    throw new Error("Usage: bun .archon/scripts/factory-schedule.ts <install|remove|status> [project-root]");
  }
  console.log(JSON.stringify({ action, platform, id, result }, null, 2));
}

if (import.meta.main) main();
