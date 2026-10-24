#!/usr/bin/env node
/**
 * Generic browser driver for the test drive. Logs in as a real user through the app's own
 * login path (no injected sessions), then takes a screenshot or runs a script you wrote.
 * Everything app-specific (login steps, theme switch, viewport) comes from browser.config.json
 * next to this file, so the driver itself can move to another repository unchanged.
 *
 *   node drive.mjs shot --base <url> --as <user> --path </route> --out <file.png> [--theme dark] [--full] [--no-login]
 *   node drive.mjs run  --base <url> --as <user> --out-dir <dir> [--theme dark] <script.mjs>
 *
 * A script exports `default async ({ page, base, user, shot, log }) => { ... }`:
 *   shot('name')  saves <out-dir>/<name>.png and returns its absolute path
 *   log('text')   appends to <out-dir>/drive.log
 * The driver always writes <out-dir>/console.log with page errors, console errors and failed
 * requests seen during the drive, and exits 1 when the script throws.
 *
 * Password: config "password" ("$user" means password equals the username), or the environment
 * variable BROWSER_PASSWORD_<USER> when set. Never printed.
 */
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, appendFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(readFileSync(join(here, 'browser.config.json'), 'utf8'));

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--full' || a === '--no-login' || a === '--headed') out[a.slice(2)] = true;
    else if (a.startsWith('--')) out[a.slice(2)] = argv[++i];
    else out._.push(a);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const mode = args._[0];
if (!['shot', 'run'].includes(mode) || !args.base || !args.as) {
  console.error('usage: drive.mjs shot --base <url> --as <user> --path </route> --out <file.png> [--theme dark] [--full]\n       drive.mjs run --base <url> --as <user> --out-dir <dir> [--theme dark] <script.mjs>');
  process.exit(2);
}
const base = args.base.replace(/\/$/, '');
const user = args.as;
const outDir = resolve(mode === 'run' ? (args['out-dir'] || '.') : dirname(args.out));
mkdirSync(outDir, { recursive: true });
const consolePath = join(outDir, 'console.log');
const logPath = join(outDir, 'drive.log');
writeFileSync(consolePath, `# ${new Date().toISOString()} ${mode} as ${user} at ${base}\n`);
const log = (text) => appendFileSync(logPath, `${new Date().toISOString()} ${text}\n`);
const note = (text) => appendFileSync(consolePath, `${text}\n`);

function password() {
  const env = process.env[`BROWSER_PASSWORD_${user.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`];
  if (env) return env;
  const p = config.password ?? '$user';
  return p === '$user' ? user : p;
}
const subst = (v) => (v === '$user' ? user : v === '$password' ? password() : v);

async function login(page) {
  const l = config.login;
  await page.goto(base + (l.path || '/'), { waitUntil: 'domcontentloaded' });
  for (const step of l.steps || []) {
    const timeout = step.optional ? 4000 : 20000;
    try {
      if (step.goto) await page.goto(step.goto.startsWith('http') ? step.goto : base + step.goto);
      if (step.waitFor) await page.waitForSelector(step.waitFor, { timeout });
      if (step.click) await page.locator(step.click).first().click({ timeout });
      if (step.fill) await page.locator(step.fill).first().fill(subst(step.value ?? ''), { timeout });
    } catch (e) {
      if (step.optional) continue;
      throw new Error(`login step failed (${JSON.stringify({ ...step, value: step.value ? '…' : undefined })}): ${e.message}`);
    }
  }
  const done = l.done || {};
  if (done.urlNot) await page.waitForURL((u) => !u.pathname.startsWith(done.urlNot), { timeout: 30000 });
  if (done.selector) await page.waitForSelector(done.selector, { timeout: 30000 });
  log(`logged in as ${user}`);
}

async function applyTheme(page, name) {
  if (!name) return;
  const t = (config.themes || {})[name];
  if (!t) throw new Error(`theme '${name}' is not in browser.config.json`);
  if (t.localStorage) {
    await page.evaluate((kv) => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); }, t.localStorage);
  }
  if (t.reload) await page.reload({ waitUntil: 'networkidle' });
  log(`theme ${name}`);
}

const browser = await chromium.launch({ headless: !args.headed });
const context = await browser.newContext({ viewport: config.viewport || { width: 1280, height: 800 } });
const page = await context.newPage();
page.on('pageerror', (e) => note(`PAGE ERROR ${e.message}`));
page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) note(`CONSOLE ${m.type().toUpperCase()} ${m.text()}`); });
page.on('requestfailed', (r) => note(`REQUEST FAILED ${r.method()} ${r.url()} ${r.failure()?.errorText ?? ''}`));
page.on('response', (r) => { if (r.status() >= 400) note(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`); });

let code = 0;
try {
  if (!args['no-login']) await login(page);
  if (mode === 'shot') {
    if (!args.path || !args.out) throw new Error('shot needs --path and --out');
    await page.goto(base + args.path, { waitUntil: 'networkidle' });
    await applyTheme(page, args.theme);
    await page.waitForTimeout(300);
    const out = resolve(args.out);
    await page.screenshot({ path: out, fullPage: !!args.full });
    console.log(JSON.stringify({ screenshot: out, url: page.url(), title: await page.title(), console: consolePath }));
  } else {
    const script = args._[1];
    if (!script) throw new Error('run needs a script path');
    await applyTheme(page, args.theme);
    const mod = await import(pathToFileURL(resolve(script)).href);
    let n = 0;
    const shot = async (name, opts = {}) => {
      const file = join(outDir, `${String(++n).padStart(2, '0')}-${name.replace(/[^A-Za-z0-9_-]+/g, '-')}.png`);
      await page.screenshot({ path: file, fullPage: !!opts.full });
      log(`shot ${file} at ${page.url()}`);
      return file;
    };
    await mod.default({ page, context, base, user, shot, log });
    console.log(JSON.stringify({ ok: true, outDir, console: consolePath, log: logPath }));
  }
} catch (e) {
  code = 1;
  note(`DRIVE FAILED ${e.message}`);
  try { await page.screenshot({ path: join(outDir, 'failure.png') }); } catch {}
  console.error(`drive failed: ${e.message}\nsee ${consolePath} and ${join(outDir, 'failure.png')}`);
} finally {
  await browser.close();
}
process.exit(code);
