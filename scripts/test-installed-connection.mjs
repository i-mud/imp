import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

// The PowerShell caller verifies that this listener belongs to its isolated WebView.
const endpoint = `http://127.0.0.1:${Number(process.argv[2])}`;
const [phase, tunnelConfig] = process.argv.slice(3);
setTimeout(() => {
  console.error('Installed connection UI acceptance timed out');
  process.exit(1);
}, 60_000).unref();

async function waitFor(probe, description) {
  const deadline = Date.now() + 12_000;
  do {
    const result = await probe();
    if (result) return result;
    await delay(100);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${description}`);
}

const debuggerUrl = await waitFor(async () => {
  try {
    const response = await fetch(`${endpoint}/json/list`, { signal: AbortSignal.timeout(1000) });
    const pages = await response.json();
    return pages.find((page) => page.type === 'page' && page.url === 'http://tauri.localhost/')
      ?.webSocketDebuggerUrl;
  } catch {
    return null; // The browser listener can come up before its page is registered.
  }
}, 'the installed Imp page');
assert.equal(new URL(debuggerUrl).host, new URL(endpoint).host);
const socket = new WebSocket(debuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
function cdp(method, params) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    function receive({ data }) {
      const response = JSON.parse(data);
      if (response.id !== id) return;
      socket.removeEventListener('message', receive);
      if (response.error) reject(new Error(`${method} failed: ${response.error.message}`));
      else resolve(response.result);
    }
    socket.addEventListener('message', receive);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const { result, exceptionDetails } = await cdp('Runtime.evaluate', { expression, returnByValue: true });
  assert.equal(exceptionDetails, undefined, 'Reading the installed page failed');
  return result.value;
}

function visibleText(text) {
  return waitFor(() => evaluate(`document.body?.innerText.includes(${JSON.stringify(text)})`), text);
}

async function click(selector, text) {
  const point = await waitFor(
    () =>
      evaluate(`(() => {
      const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find((candidate) =>
        candidate.getClientRects().length && !candidate.disabled &&
        (${JSON.stringify(text ?? null)} === null || candidate.textContent.trim() === ${JSON.stringify(text ?? null)}));
      if (!element) return null;
      element.scrollIntoView({block: 'center'});
      const {x, y, width, height} = element.getBoundingClientRect();
      const center = {x: x + width / 2, y: y + height / 2};
      return element.contains(document.elementFromPoint(center.x, center.y)) ? center : null;
    })()`),
    text ?? selector,
  );
  await cdp('Input.dispatchMouseEvent', { ...point, type: 'mousePressed', button: 'left', clickCount: 1 });
  await cdp('Input.dispatchMouseEvent', { ...point, type: 'mouseReleased', button: 'left', clickCount: 1 });
}

const dialogText = () => evaluate(`document.querySelector('[role="dialog"]')?.innerText ?? ''`);
const dialogShows = (text) =>
  waitFor(async () => (await dialogText()).includes(text), `dialog text: ${text}`);
const dialogLacks = async (text) => assert.ok(!(await dialogText()).includes(text), `Unexpected: ${text}`);
const choose = (name, value) => click(`label:has(input[name="${name}"][value="${value}"])`);

async function openManager() {
  const entry = await waitFor(
    () =>
      evaluate(`(() => {
        const visible = (label) => [...document.querySelectorAll('button')].some((button) =>
          button.getClientRects().length && (button.textContent.trim() === label || button.ariaLabel === label));
        return ['Set up connection', 'Review connection settings', 'Settings'].find(visible) ?? null;
      })()`),
    'a connection manager entry',
  );
  if (entry === 'Settings') {
    await click('button[aria-label="Settings"]');
    await click('button[aria-label="Manage connection"]');
  } else {
    await click('button', entry);
  }
  await waitFor(
    () => evaluate(`document.querySelector('[role="dialog"]')?.contains(document.activeElement)`),
    'focus inside the connection manager',
  );
  await assertFits();
}

const closeManager = () => click('[role="dialog"] button', 'Close');
const savedMode = () => JSON.parse(readFileSync(tunnelConfig, 'utf8')).mode;
const checked = (name) => evaluate(`document.querySelector('input[name="${name}"]:checked')?.value ?? null`);
const field = (label) =>
  evaluate(`(() => [...document.querySelectorAll('[role="dialog"] label')]
    .find((candidate) => candidate.querySelector('span')?.textContent.trim() === ${JSON.stringify(label)})
    ?.querySelector('input')?.value ?? null)()`);

async function key(name, code) {
  for (const type of ['rawKeyDown', 'keyUp']) {
    await cdp('Input.dispatchKeyEvent', { type, key: name, code: name, windowsVirtualKeyCode: code });
  }
}

// Every visible dialog control must be reachable inside the HUD window and its dialog.
async function assertFits() {
  await waitFor(
    () =>
      evaluate(`(() => {
    const dialog = document.querySelector('[role="dialog"]').getBoundingClientRect();
    const within = (box, outer) => box.left >= outer.left - 1 && box.right <= outer.right + 1 &&
      box.top >= outer.top - 1 && box.bottom <= outer.bottom + 1;
    const viewport = {left: 0, top: 0, right: innerWidth, bottom: innerHeight};
    if (!within(dialog, viewport)) return false;
    for (const control of document.querySelectorAll('[role="dialog"] :is(input, button, summary)')) {
      if (!control.getClientRects().length) continue;
      // The native window resizes asynchronously when the selected form changes.
      const box = control.getBoundingClientRect();
      if (!within(box, viewport) || !within(box, dialog)) return false;
    }
    return true;
  })()`),
    'the manager and its controls to fit the native window',
  );
}

try {
  if (phase === 'first') {
    await openManager();
    await dialogShows('No connection configured');
    await choose('client-location', 'remote');
    await choose('remote-method', 'managed');
    await dialogShows('SSH target');
    await choose('remote-method', 'direct');
    await dialogShows('Pairing token');
    await click('summary', 'Advanced');
    await choose('remote-method', 'external');
    await closeManager(); // Nothing was entered or saved.
    await click('button', 'Not now');
    await visibleText('Open Settings → Connection → Manage to set up a connection.');
  } else if (phase === 'deferred') {
    await visibleText('Open Settings → Connection → Manage to set up a connection.'); // Dismissal persisted.
    await openManager();
    await choose('client-location', 'local');
    await click('button[type="submit"]', 'Save');
    await dialogShows('Saved for next launch');
    await dialogShows('Saved. Restart Imp to use this connection.');
    assert.equal(savedMode(), 'local');
  } else if (['local', 'managed', 'external', 'direct'].includes(phase)) {
    const label = {
      local: 'This computer',
      managed: 'Server login',
      external: 'Own SSH tunnel',
      direct: 'Remote Imp credentials',
    }[phase];
    const seeded = phase === 'local' ? null : JSON.parse(readFileSync(tunnelConfig, 'utf8'));
    await openManager();
    await dialogShows('In use');
    await dialogShows(label);
    await dialogLacks('Saved for next launch');
    assert.equal(await checked('client-location'), phase === 'local' ? 'local' : 'remote');
    if (phase === 'local') {
      // Keyboard dismissal: Escape closes the dialog and focus returns to an app control.
      await key('Escape', 27);
      await waitFor(
        () => evaluate(`!document.querySelector('[role="dialog"]')`),
        'Escape to close the manager',
      );
      assert.equal(await evaluate(`document.activeElement !== document.body`), true);
    } else if (phase === 'managed') {
      assert.equal(await checked('remote-method'), 'managed');
      assert.equal(await field('SSH target'), seeded.sshTarget);
      await evaluate(`[...document.querySelectorAll('[role="dialog"] input[type="text"]')][0].select()`);
      await key('Backspace', 8);
      await click('button[type="submit"]', 'Save');
      await waitFor(
        () => evaluate(`document.activeElement?.getAttribute('aria-invalid') === 'true'`),
        'invalid SSH field focus',
      );
      await assertFits();
    } else if (phase === 'external') {
      assert.equal(await checked('remote-method'), 'external');
      assert.equal(await evaluate(`document.querySelector('details').open`), true);
    } else {
      assert.equal(await checked('remote-method'), 'direct');
      assert.equal(await field('WSS state URL'), seeded.remoteUrl);
      assert.equal(await field('Pairing token'), '');
      await click('button[type="submit"]', 'Save'); // Blank token on the same endpoint keeps the credential.
      await dialogShows('Connection settings saved.');
      assert.equal(JSON.parse(readFileSync(tunnelConfig, 'utf8')).pairingToken, seeded.pairingToken);
      await choose('client-location', 'local');
      await click('button[type="submit"]', 'Save');
      await click('button', 'Remove credentials and save');
      await dialogShows('Saved for next launch');
      assert.equal(savedMode(), 'local');
    }
  } else if (phase === 'invalid') {
    await openManager();
    await dialogShows('Imp could not read tunnel.json');
    await dialogLacks('Where does your MUD client run?');
  } else {
    throw new Error(`Unknown phase ${phase}`);
  }
  process.stdout.write(`Installed connection UI phase ${phase} passed.\n`);
} finally {
  socket.close();
}
