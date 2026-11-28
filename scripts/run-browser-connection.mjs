// Disposable controlled-Chromium acceptance for the connection manager. Usage:
//   CHROME=/path/to/chrome node scripts/run-browser-connection.mjs <browser-direct|browser-managed> [screenshot-dir]
// Starts its own Vite dev server on 1420 (refuses an occupied port) and a headless Chromium with a
// throwaway profile and loopback-only debugger, runs the phase against the exact nonce page, then removes both.
// Set CHROME_NO_SANDBOX=1 only inside an already isolated container that cannot run Chromium's sandbox.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const [phase, screenshots] = process.argv.slice(2);
const chrome = process.env.CHROME;
if (!['browser-direct', 'browser-managed'].includes(phase) || !chrome) {
  console.error(
    'Usage: CHROME=<chromium> node scripts/run-browser-connection.mjs <browser-direct|browser-managed> [screenshot-dir]',
  );
  process.exit(2);
}
if (screenshots) mkdirSync(screenshots, { recursive: true });

const root = fileURLToPath(new URL('..', import.meta.url));
const profile = mkdtempSync(join(tmpdir(), 'imp-browser-connection-'));
const nonce = randomBytes(12).toString('hex');
const children = [];

function start(command, args, options) {
  // Each child leads its own process group so only what this run started is stopped, including Vite behind npm.
  const child = spawn(command, args, { stdio: 'ignore', detached: true, ...options });
  child.exited = new Promise((resolve) => child.once('exit', resolve));
  children.push(child);
  return child;
}

async function stop(child) {
  for (const signal of ['SIGTERM', 'SIGKILL']) {
    if (child.exitCode !== null || child.signalCode !== null) return true;
    try {
      process.kill(-child.pid, signal);
    } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
    if ((await Promise.race([child.exited.then(() => true), delay(10_000, false)])) === true) return true;
  }
  return false;
}

const serving = () =>
  fetch('http://localhost:1420/', { signal: AbortSignal.timeout(1000) }).then(
    () => true,
    () => false,
  );

async function until(probe, description) {
  for (let attempt = 0; attempt < 150; attempt++) {
    const result = await probe();
    if (result) return result;
    await delay(200);
  }
  throw new Error(`Timed out waiting for ${description}`);
}

let code = 1;
try {
  if (await serving())
    throw new Error('Port 1420 is already serving; refusing to drive a server this run does not own');
  const vite = start('npm', ['run', 'dev', '--workspace', '@imp/desktop'], { cwd: root });
  await until(async () => {
    if (vite.exitCode !== null) throw new Error('The Vite dev server exited (is 1420 occupied?)');
    return serving();
  }, 'the Vite dev server');
  if (vite.exitCode !== null) throw new Error('The Vite dev server exited (is 1420 occupied?)');

  // Port 0 lets Chromium choose and publish its own debugger port, so there is no probe-and-rebind race.
  const chromium = start(chrome, [
    '--headless=new',
    ...(process.env.CHROME_NO_SANDBOX === '1' ? ['--no-sandbox'] : []),
    '--no-first-run',
    '--disable-extensions',
    `--user-data-dir=${profile}`,
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0',
    `http://localhost:1420/?managed-check-fixture=${nonce}`,
  ]);
  const activePort = join(profile, 'DevToolsActivePort');
  const port = await until(async () => {
    if (chromium.exitCode !== null) throw new Error('Chromium exited before publishing its debugger port');
    return existsSync(activePort) && Number(readFileSync(activePort, 'utf8').split('\n')[0]);
  }, 'the Chromium debugger port');

  const driver = start(
    process.execPath,
    [
      join(root, 'scripts/test-installed-connection.mjs'),
      String(port),
      phase,
      nonce,
      '0',
      screenshots ?? '-',
    ],
    { stdio: 'inherit' },
  );
  code = await new Promise((resolve) => driver.on('exit', (status) => resolve(status ?? 1)));
} catch (error) {
  console.error(error.message);
} finally {
  const stopped = await Promise.all(children.reverse().map(stop));
  if (stopped.every(Boolean)) {
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } else {
    // Never delete a profile a live browser may still hold.
    console.error(`A child did not exit; left ${profile} in place`);
    code = 1;
  }
}
process.exit(code);
