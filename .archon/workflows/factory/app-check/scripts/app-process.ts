import { closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const RECORD_VERSION = 1;
const WINDOWS_PROCESS_SCRIPT = String.raw`
$ErrorActionPreference = 'Stop'
$source = @'
using System;
using System.Globalization;
using System.Runtime.InteropServices;

public static class FactoryOwnedProcess
{
    private const uint PROCESS_TERMINATE = 0x0001;
    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    private const uint SYNCHRONIZE = 0x00100000;
    private const uint WAIT_OBJECT_0 = 0x00000000;
    private const uint WAIT_TIMEOUT = 0x00000102;
    private const int ERROR_INVALID_PARAMETER = 87;

    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime
    {
        public uint Low;
        public uint High;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetProcessTimes(IntPtr process, out FileTime creation, out FileTime exit, out FileTime kernel, out FileTime user);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private static ulong Value(FileTime value)
    {
        return ((ulong)value.High << 32) | value.Low;
    }

    private static string OpenError()
    {
        int error = Marshal.GetLastWin32Error();
        return error == ERROR_INVALID_PARAMETER ? "already-exited" : "error:OpenProcess:" + error.ToString(CultureInfo.InvariantCulture);
    }

    public static string Inspect(uint processId)
    {
        IntPtr handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, processId);
        if (handle == IntPtr.Zero) return OpenError();
        try
        {
            FileTime creation, exit, kernel, user;
            if (!GetProcessTimes(handle, out creation, out exit, out kernel, out user))
                return "error:GetProcessTimes:" + Marshal.GetLastWin32Error().ToString(CultureInfo.InvariantCulture);
            return "identity:" + Value(creation).ToString(CultureInfo.InvariantCulture);
        }
        finally
        {
            CloseHandle(handle);
        }
    }

    public static string Stop(uint processId, ulong expectedCreationTime)
    {
        // This one handle is used for identity verification, termination, and the
        // exit wait. The PID is never looked up again after verification.
        IntPtr handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE, false, processId);
        if (handle == IntPtr.Zero) return OpenError();
        try
        {
            FileTime creation, exit, kernel, user;
            if (!GetProcessTimes(handle, out creation, out exit, out kernel, out user))
                return "error:GetProcessTimes:" + Marshal.GetLastWin32Error().ToString(CultureInfo.InvariantCulture);
            if (Value(creation) != expectedCreationTime) return "identity-mismatch";
            uint before = WaitForSingleObject(handle, 0);
            if (before == WAIT_OBJECT_0) return "already-exited";
            if (before != WAIT_TIMEOUT)
                return "error:WaitForSingleObject:" + Marshal.GetLastWin32Error().ToString(CultureInfo.InvariantCulture);
            if (!TerminateProcess(handle, 1))
            {
                int error = Marshal.GetLastWin32Error();
                return WaitForSingleObject(handle, 0) == WAIT_OBJECT_0
                    ? "already-exited"
                    : "error:TerminateProcess:" + error.ToString(CultureInfo.InvariantCulture);
            }
            uint wait = WaitForSingleObject(handle, 10000);
            if (wait == WAIT_OBJECT_0) return "stopped";
            if (wait == WAIT_TIMEOUT) return "still-running";
            return "error:WaitForSingleObject:" + Marshal.GetLastWin32Error().ToString(CultureInfo.InvariantCulture);
        }
        finally
        {
            CloseHandle(handle);
        }
    }
}
'@
Add-Type -TypeDefinition $source
if ($env:FACTORY_PROCESS_ACTION -eq 'inspect') {
  [Console]::Out.Write([FactoryOwnedProcess]::Inspect([uint32]$env:FACTORY_PROCESS_PID))
} elseif ($env:FACTORY_PROCESS_ACTION -eq 'stop') {
  [Console]::Out.Write([FactoryOwnedProcess]::Stop([uint32]$env:FACTORY_PROCESS_PID, [uint64]$env:FACTORY_PROCESS_IDENTITY))
} else {
  throw 'unknown app process action'
}
`;

// Linux holds the process by a pidfd, the counterpart of the Windows handle above, and its
// identity is the start time in /proc/<pid>/stat (clock ticks since boot). The pidfd is opened
// before /proc is read: while that process has not exited its PID cannot name another one, so
// the stat read belongs to it. Every signal goes through the pidfd, never through the PID.
const LINUX_PROCESS_SCRIPT = String.raw`
import os, select, signal

def stat(pid):
    with open("/proc/%d/stat" % pid, "rb") as handle:
        data = handle.read()
    fields = data[data.rindex(b")") + 2:].split()
    return fields[0].decode(), fields[1].decode(), fields[19].decode()

def exited(fd, milliseconds):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return bool(poller.poll(milliseconds))

def main():
    action, pid = os.environ["FACTORY_PROCESS_ACTION"], int(os.environ["FACTORY_PROCESS_PID"])
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return "already-exited"
    try:
        try:
            state, parent, start = stat(pid)
        except FileNotFoundError:
            return "already-exited"
        if state == "Z" or exited(fd, 0):
            return "already-exited"
        if action == "inspect":
            parents = os.environ.get("FACTORY_PROCESS_PARENTS", "").split(",")
            return "identity:" + start if parent in parents else "not-launcher-child"
        if action != "stop":
            raise ValueError("unknown app process action")
        with open("/proc/sys/kernel/random/boot_id") as handle:
            boot = handle.read().strip()
        if boot != os.environ["FACTORY_PROCESS_BOOT_ID"] or start != os.environ["FACTORY_PROCESS_IDENTITY"]:
            return "identity-mismatch"
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                return "stopped"
            if exited(fd, 5000):
                return "stopped"
        return "still-running"
    finally:
        os.close(fd)

try:
    result = main()
except Exception as error:
    result = "error:%s:%s" % (type(error).__name__, error)
print(result, end="")
`;

export interface AppProcessRecord {
  version: 1;
  platform: "win32" | "linux";
  pid: number;
  identity: string;
  boot_id?: string;
  recorded_at: string;
}

export type AppCleanupStatus =
  | "stopped"
  | "already-exited"
  | "no-record"
  | "invalid-record"
  | "identity-mismatch"
  | "unsupported-platform"
  | "stop-failed";

export interface AppCleanupResult {
  status: AppCleanupStatus;
  detail?: string;
}

export function appProcessRecordPath(artifacts = process.env.ARTIFACTS_DIR): string {
  if (!artifacts) throw new Error("ARTIFACTS_DIR is required for app process ownership");
  return join(artifacts, "app-check", "app-process.json");
}

export function appProcessSupport(): { supported: boolean; platform: string; limitation: string } {
  if (process.platform === "win32") {
    return { supported: true, platform: process.platform, limitation: "" };
  }
  if (process.platform === "linux") {
    return linuxPidfdAvailable()
      ? { supported: true, platform: process.platform, limitation: "" }
      : { supported: false, platform: process.platform, limitation: "Linux app process identity cleanup needs python3 with os.pidfd_open (Python 3.9+, Linux 5.3+)" };
  }
  return {
    supported: false,
    platform: process.platform,
    limitation: "race-free app process identity cleanup is currently implemented only on Windows and Linux",
  };
}

let pidfdAvailable: boolean | undefined;
function linuxPidfdAvailable(): boolean {
  pidfdAvailable ??= Bun.spawnSync(["python3", "-c", "import os, signal; signal.pidfd_send_signal; os.close(os.pidfd_open(os.getpid()))"], { stdout: "ignore", stderr: "ignore" }).exitCode === 0;
  return pidfdAvailable;
}

function linuxBootId(): string {
  return readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim();
}

function linuxProcess(action: "inspect" | "stop", pid: number, extra: Record<string, string>): string {
  const environment = { ...process.env, ...extra, FACTORY_PROCESS_ACTION: action, FACTORY_PROCESS_PID: String(pid) };
  const result = Bun.spawnSync(["python3", "-c", LINUX_PROCESS_SCRIPT], { env: environment, stdout: "pipe", stderr: "pipe" });
  if (result.exitCode !== 0) {
    throw new Error(`Linux process identity helper failed: ${result.stderr.toString().trim() || `exit ${result.exitCode}`}`);
  }
  return result.stdout.toString().trim();
}

function windowsPowerShell(): string {
  const systemRoot = process.env.SystemRoot || process.env.SYSTEMROOT;
  if (!systemRoot) throw new Error("SystemRoot is unavailable; cannot verify Windows process identity");
  const executable = join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  if (!existsSync(executable)) throw new Error("Windows PowerShell is unavailable; cannot verify process identity");
  return executable;
}

function windowsProcess(action: "inspect" | "stop", pid: number, identity?: string): string {
  const environment = { ...process.env, FACTORY_PROCESS_ACTION: action, FACTORY_PROCESS_PID: String(pid) };
  if (identity) environment.FACTORY_PROCESS_IDENTITY = identity;
  const result = Bun.spawnSync(
    [windowsPowerShell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PROCESS_SCRIPT],
    { env: environment, stdout: "pipe", stderr: "pipe", windowsHide: true },
  );
  if (result.exitCode !== 0) {
    throw new Error(`Windows process identity helper failed: ${result.stderr.toString().trim() || `exit ${result.exitCode}`}`);
  }
  return result.stdout.toString().trim();
}

function validRecord(value: unknown): value is AppProcessRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (record.version !== RECORD_VERSION || (record.platform !== "win32" && record.platform !== "linux")) return false;
  if (record.platform === "linux" && (typeof record.boot_id !== "string" || !/^[0-9a-f-]{36}$/.test(record.boot_id))) return false;
  if (!Number.isInteger(record.pid) || (record.pid as number) <= 0 || (record.pid as number) > 0xffff_ffff) return false;
  if (typeof record.identity !== "string" || !/^[1-9]\d{0,19}$/.test(record.identity)) return false;
  return typeof record.recorded_at === "string" && !Number.isNaN(Date.parse(record.recorded_at));
}

function retireRecord(path: string): void {
  rmSync(path, { force: true });
}

export function inspectAppProcessIdentity(pid: number): string {
  const support = appProcessSupport();
  if (!support.supported) throw new Error(support.limitation);
  if (!Number.isInteger(pid) || pid <= 0 || pid > 0xffff_ffff || pid === process.pid) throw new Error("app PID must identify a positive child process");
  // On Linux the app must be a child of this process, or of the shell that launched both it
  // and this helper in one command: that parent link stands in for the retained launcher handle.
  const inspected = process.platform === "win32"
    ? windowsProcess("inspect", pid)
    : linuxProcess("inspect", pid, { FACTORY_PROCESS_PARENTS: `${process.pid},${process.ppid}` });
  if (inspected === "not-launcher-child") throw new Error("app PID is not a child of the shell that launched it");
  if (!inspected.startsWith("identity:")) {
    throw new Error(inspected === "already-exited" ? "app process exited before its identity was recorded" : `could not establish app process identity: ${inspected}`);
  }
  const identity = inspected.slice("identity:".length);
  if (!/^[1-9]\d{0,19}$/.test(identity)) throw new Error(`${process.platform === "win32" ? "Windows" : "Linux"} returned a malformed app process identity`);
  return identity;
}

export function recordAppProcess(pid: number, launcherIdentity: string, path = appProcessRecordPath()): AppProcessRecord {
  if (!/^[1-9]\d{0,19}$/.test(launcherIdentity)) throw new Error(`launcher did not provide a valid ${process.platform === "win32" ? "Windows" : "Linux"} process identity`);
  if (existsSync(path)) throw new Error(`app process record already exists: ${path}`);

  const identity = inspectAppProcessIdentity(pid);
  if (identity !== launcherIdentity) throw new Error("app PID identity no longer matches the retained launcher handle");

  const record: AppProcessRecord = process.platform === "win32"
    ? { version: 1, platform: "win32", pid, identity, recorded_at: new Date().toISOString() }
    : { version: 1, platform: "linux", pid, identity, boot_id: linuxBootId(), recorded_at: new Date().toISOString() };
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(record)}\n`, { flag: "wx" });
  try {
    renameSync(temporary, path);
  } catch (error) {
    rmSync(temporary, { force: true });
    throw error;
  }
  return record;
}

// Linux has no launcher handle an agent's shell can retain: a shell with job control puts a
// background job in its own process group, so setsid forks and the PID it reports is gone.
// The helper starts the app itself instead, as its own child in a new session, and records it
// while it is still the parent. The app inherits the environment (port, database) and cwd.
export function launchAppProcess(command: string, log: string, path = appProcessRecordPath()): AppProcessRecord {
  if (process.platform !== "linux") throw new Error("launch is for Linux; on Windows retain the Start-Process -PassThru object and use record");
  const support = appProcessSupport();
  if (!support.supported) throw new Error(support.limitation);
  if (!command.trim()) throw new Error("launch needs the app start command");
  if (existsSync(path)) throw new Error(`app process record already exists: ${path}`);
  mkdirSync(dirname(log), { recursive: true });
  const output = openSync(log, "a");
  let child: Bun.Subprocess;
  try {
    // bash runs a lone simple command in its own place, so the PID is the app's; a compound
    // command keeps bash as the recorded process.
    child = Bun.spawn(["bash", "-c", command], { detached: true, stdin: "ignore", stdout: output, stderr: output });
  } finally {
    closeSync(output);
  }
  try {
    const record = recordAppProcess(child.pid, inspectAppProcessIdentity(child.pid), path);
    child.unref();
    return record;
  } catch (error) {
    // Still this helper's unreaped child, so the subprocess object names it alone.
    child.kill("SIGKILL");
    throw error;
  }
}

export function stopRecordedApp(path = appProcessRecordPath()): AppCleanupResult {
  // Old packages wrote only this numeric file. Retire it without reading it or
  // treating it as authority to signal any process.
  rmSync(join(dirname(path), "app.pid"), { force: true });
  if (!existsSync(path)) return { status: "no-record" };

  let value: unknown;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return { status: "invalid-record", detail: "app process record is not valid JSON" };
  }
  if (!validRecord(value) || value.pid === process.pid) {
    return { status: "invalid-record", detail: "app process record has no valid versioned identity" };
  }
  const support = appProcessSupport();
  if (!support.supported || value.platform !== process.platform) {
    return { status: "unsupported-platform", detail: support.limitation || `record was written on ${value.platform}, not ${process.platform}` };
  }

  let outcome: string;
  try {
    outcome = value.platform === "win32"
      ? windowsProcess("stop", value.pid, value.identity)
      : linuxProcess("stop", value.pid, { FACTORY_PROCESS_IDENTITY: value.identity, FACTORY_PROCESS_BOOT_ID: value.boot_id! });
  } catch (error) {
    return { status: "stop-failed", detail: error instanceof Error ? error.message : String(error) };
  }
  if (outcome === "stopped" || outcome === "already-exited") {
    retireRecord(path);
    return { status: outcome };
  }
  if (outcome === "identity-mismatch") {
    return { status: "identity-mismatch", detail: "PID now belongs to a different process; no signal was sent" };
  }
  return { status: "stop-failed", detail: outcome === "still-running" ? "owned app did not exit after termination" : outcome };
}

if (import.meta.main) {
  const action = process.argv[2];
  if (action === "support") {
    console.log(JSON.stringify(appProcessSupport()));
  } else if (action === "launch") {
    const record = launchAppProcess(process.argv[4] || "", process.argv[3] || "");
    console.log(JSON.stringify({ recorded: true, pid: record.pid, platform: record.platform }));
  } else if (action === "inspect") {
    console.log(inspectAppProcessIdentity(Number(process.argv[3])));
  } else if (action === "record") {
    const record = recordAppProcess(Number(process.argv[3]), process.argv[4] || "");
    console.log(JSON.stringify({ recorded: true, pid: record.pid, platform: record.platform }));
  } else if (action === "stop") {
    const result = stopRecordedApp();
    console.log(JSON.stringify(result));
    if (!["stopped", "already-exited", "no-record"].includes(result.status)) process.exitCode = 1;
  } else {
    throw new Error("usage: app-process.ts support | launch <log-file> <start-command> | inspect <pid> | record <pid> <launcher-identity> | stop");
  }
}
