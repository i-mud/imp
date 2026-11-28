import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { isDeepStrictEqual } from 'node:util';

import { setupManagedCheckBrowser } from './managed-check-browser-fixture.mjs';

// PowerShell verifies installed WebView ownership; the browser mode needs a dedicated headless profile.
const endpoint = `http://127.0.0.1:${Number(process.argv[2])}`;
const [phase, tunnelConfig, fixturePort, screenshotArgument, diagnosticFixture] = process.argv.slice(3);
const screenshotDirectory = screenshotArgument === '-' ? undefined : screenshotArgument;
const browser = phase === 'browser-managed';
if (browser && !/^[a-f0-9]{24}$/.test(tunnelConfig ?? '')) {
  throw new Error('Browser acceptance requires its disposable page nonce');
}
const pageUrl = browser
  ? `http://localhost:1420/?managed-check-fixture=${tunnelConfig}`
  : 'http://tauri.localhost/';
setTimeout(() => {
  console.error('Connection UI acceptance timed out');
  process.exit(1);
}, 150_000).unref();

async function waitFor(probe, description) {
  const deadline = Date.now() + 12_000;
  do {
    const result = await probe();
    if (result) return result;
    await delay(100);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${description}`);
}

const debuggerUrl = await waitFor(
  async () => {
    try {
      const response = await fetch(`${endpoint}/json/list`, { signal: AbortSignal.timeout(1000) });
      const pages = await response.json();
      return pages.find((page) => page.type === 'page' && page.url === pageUrl)?.webSocketDebuggerUrl;
    } catch {
      return null; // The browser listener can come up before its page is registered.
    }
  },
  browser ? 'the disposable Chromium fixture page' : 'the installed Imp page',
);
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
  const { result, exceptionDetails } = await cdp('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  ok(exceptionDetails === undefined, 'Reading the test page failed');
  return result.value;
}

let browserWidth;
async function syncBrowserViewport() {
  if (!browser) return;
  const width = (await evaluate('window.managedCheckFixture?.size?.Logical.width')) ?? 320;
  if (width === browserWidth) return;
  await cdp('Emulation.setDeviceMetricsOverride', {
    width,
    height: 850,
    deviceScaleFactor: 1,
    mobile: false,
  });
  browserWidth = width;
}

function visibleText(text) {
  return waitFor(() => evaluate(`document.body?.innerText.includes(${JSON.stringify(text)})`), text);
}

async function click(selector, text) {
  await syncBrowserViewport();
  const point = await waitFor(
    () =>
      evaluate(`(async () => {
      const element = [...document.querySelectorAll(${JSON.stringify(selector)})].find((candidate) =>
        candidate.getClientRects().length && !candidate.disabled &&
        (${JSON.stringify(text ?? null)} === null || candidate.textContent.trim() === ${JSON.stringify(text ?? null)}));
      if (!element) return null;
      element.scrollIntoView({block: 'nearest'});
      // Route and validation changes resize the native window after the DOM changes.
      const before = element.getBoundingClientRect();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const {x, y, width, height} = element.getBoundingClientRect();
      if (x !== before.x || y !== before.y || width !== before.width || height !== before.height) return null;
      const center = {x: x + width / 2, y: y + height / 2};
      return element.contains(document.elementFromPoint(center.x, center.y)) ? center : null;
    })()`),
    text ?? selector,
  );
  await cdp('Input.dispatchMouseEvent', { ...point, type: 'mousePressed', button: 'left', clickCount: 1 });
  await cdp('Input.dispatchMouseEvent', { ...point, type: 'mouseReleased', button: 'left', clickCount: 1 });
}

const J = JSON.stringify;
const DIALOG = '.connection-dialog';
const SSH = `${DIALOG} input[name="ssh-target"]`;
const SSH_HOST = `${DIALOG} input[name="ssh-host"]`;
const SSH_USER = `${DIALOG} input[name="ssh-username"]`;
const SSH_PORT = `${DIALOG} input[name="ssh-port"]`;
const SSH_ENTRY = `${DIALOG} button[name="ssh-representation"]`;
const CHECK = `${DIALOG} button[name="check-managed-connection"]`;
const CHECK_STATUS = `${DIALOG} #managed-check-status[role="status"]`;
const CHECK_COPY = {
  progress: 'Checking connection…',
  live: 'The remote Imp node reported live state during this check.',
  down: 'The remote Imp node reported its feed is down.',
  noState: 'No usable state was received.',
  failure: 'SSH could not reach the remote Imp port.',
  cancelled: 'Connection check cancelled.',
  unreachable: 'Could not check the connection.',
  timeout: 'The check timed out.',
  authentication: 'SSH sign-in failed.',
  hostVerification: 'SSH could not verify the server.',
};
const URL_FIELD = `${DIALOG} input[type="url"]`;
const TOKEN = `${DIALOG} input[type="password"]`;
const MANAGE = 'button[aria-label="Manage connection"]';
const MANAGER_TRIGGER = '[data-connection-manager-trigger]';
const SETUP_TRIGGER = '[data-connection-setup-trigger]';
const SAVED = 'Connection settings saved.';
const SAVED_PENDING = 'Saved. Restart Imp to use this connection.';
const PENDING = 'Saved for next launch';
const DEFERRED_HINT = 'Open Settings → Connection → Manage to set up a connection.';
const DESTINATION = { host: '127.0.0.1', username: 'imp-fixture', port: Number(fixturePort) };
const DESTINATION_LABEL = `${DESTINATION.username}@${DESTINATION.host}:${DESTINATION.port}`;
// Fixture-only synthetic credentials; each ends in a character the pairing-token format accepts.
const TOKEN_A = 'Q'.repeat(43);
const TOKEN_B = 'g'.repeat(43);

// Failure messages must never carry configuration values, so checks pass a fixed message.
const ok = (condition, message) => assert.ok(condition, message);

const dialogText = () => evaluate(`document.querySelector(${J(DIALOG)})?.innerText ?? ''`);
const dialogShows = (text) =>
  waitFor(async () => (await dialogText()).includes(text), `dialog text: ${text}`);
const summaryShows = (heading, method, destination = '') =>
  waitFor(
    () =>
      evaluate(`Array.from(document.querySelectorAll(${J(`${DIALOG} .connection-summary > div`)}))
    .some(row => row.querySelector('dt')?.textContent.trim() === ${J(heading)}
      && row.querySelector('dd')?.textContent.trim().startsWith(${J(method)})
      && (row.querySelector('dd span')?.textContent.trim() ?? '') === ${J(destination)})`),
    `${heading} connection summary`,
  );
const dialogLacks = async (text) =>
  ok(!(await dialogText()).includes(text), `Unexpected dialog text: ${text}`);
const bodyLacks = async (text) =>
  ok(!(await evaluate(`document.body.innerText`)).includes(text), `Unexpected page text: ${text}`);
const choose = (name, value) => click(`label:has(input[name="${name}"][value="${value}"])`);
const visible = (selector) =>
  evaluate(
    `[...document.querySelectorAll(${J(selector)})].some((element) => element.getClientRects().length)`,
  );
const checked = (name) => evaluate(`document.querySelector('input[name="${name}"]:checked')?.value ?? null`);
const value = (selector) => evaluate(`document.querySelector(${J(selector)})?.value ?? null`);
const active = (selector) => evaluate(`document.activeElement?.matches(${J(selector)}) ?? false`);
const focusIs = (selector, description) => waitFor(() => active(selector), `focus on ${description}`);
const saveDisabled = () =>
  evaluate(`document.querySelector(${J(`${DIALOG} button[type="submit"]`)}).disabled`);
const noFields = async () =>
  ok(
    !(await visible(`${DIALOG} :is(input[type="text"], input[type="url"], input[type="password"])`)),
    'Unexpected connection fields',
  );
async function save() {
  // Observe a real form submission; a CDP mouse acknowledgement alone doesn't prove activation.
  const submitted = evaluate(`new Promise(resolve => {
    const form = document.querySelector('.connection-dialog form');
    const done = () => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => { form.removeEventListener('submit', done, true); resolve(false); }, 12000);
    form.addEventListener('submit', done, {capture: true, once: true});
  })`);
  await click(`${DIALOG} button[type="submit"]`, 'Save');
  ok(await submitted, 'Save did not receive a form submission');
}
const cancel = () => click(`${DIALOG} button`, 'Cancel');

const rawConfig = () => readFileSync(tunnelConfig, 'utf8');
const config = () => JSON.parse(rawConfig());
const configIs = (expected) => {
  const saved = config();
  return (
    ['mode', 'sshTarget', 'remoteUrl', 'pairingToken'].every(
      (name) => (saved[name] ?? null) === (expected[name] ?? null),
    ) && isDeepStrictEqual(saved.sshDestination, expected.sshDestination)
  );
};
const bytesAre = (expected) => rawConfig() === expected;

async function assertNoSecrets(secrets = [], typing = false) {
  const forbidden = secrets.flatMap((secret) => [
    secret,
    createHash('sha256').update(secret).digest('hex'),
    createHash('sha256').update(secret).digest('base64url'),
  ]);
  const leaked = await evaluate(`(() => {
    const token = document.querySelector('input[type="password"]');
    const text = [document.body.innerText, ...Array.from(document.querySelectorAll('input:not([type=password])'), el => el.value)].join('\\n');
    return ${J(forbidden)}.some(value => text.includes(value)) || (${J(!typing)} && token && token.value !== '');
  })()`);
  ok(!leaked, 'A credential or digest is visible in the page');
}

// Real key events: key name, virtual key code, optional text that makes the browser treat it as typing.
const KEYS = { Tab: [9], Enter: [13, '\r'], Escape: [27], Backspace: [8], ' ': [32, ' ', 'Space'] };
async function press(name, shift = false) {
  const [windowsVirtualKeyCode, text, code = name] = KEYS[name];
  const modifiers = shift ? 8 : 0;
  const base = { key: name, code, windowsVirtualKeyCode, modifiers };
  await cdp('Input.dispatchKeyEvent', { ...base, type: text ? 'keyDown' : 'rawKeyDown', text });
  await cdp('Input.dispatchKeyEvent', { ...base, type: 'keyUp' });
}

async function typeInto(selector, text) {
  await click(selector);
  await evaluate(`document.querySelector(${J(selector)}).select()`);
  if (text) await cdp('Input.insertText', { text });
  else await press('Backspace');
}

// Every visible dialog control must be reachable inside the HUD window and its dialog.
async function assertFits() {
  await waitFor(async () => {
    await syncBrowserViewport();
    return evaluate(`(() => {
    const dialog = document.querySelector(${J(DIALOG)}).getBoundingClientRect();
    const within = (box, outer) => box.left >= outer.left - 1 && box.right <= outer.right + 1 &&
      box.top >= outer.top - 1 && box.bottom <= outer.bottom + 1;
    const nativeSize = window.managedCheckFixture?.size?.Logical;
    const viewport = {left: 0, top: 0, right: innerWidth, bottom: innerHeight};
    if (!within(dialog, viewport)) return false;
    if (${J(browser)} && (!nativeSize || nativeSize.width !== Math.ceil(dialog.width) ||
      nativeSize.height !== Math.ceil(dialog.height))) return false;
    if (${J(phase === 'managed-diagnostic')} &&
      (Math.abs(innerWidth - dialog.width) > 1 || Math.abs(innerHeight - dialog.height) > 2)) return false;
    for (const control of document.querySelectorAll(${J(DIALOG)} + ' :is(input, button, summary, #managed-check-help, #managed-check-status)')) {
      if (!control.getClientRects().length) continue;
      // The native window resizes asynchronously when the selected form changes.
      const box = control.getBoundingClientRect();
      if (!within(box, viewport) || !within(box, dialog)) return false;
    }
    return true;
      })()`);
  }, 'the manager and its controls to fit the native window');
}

async function screenshot(filename) {
  await assertFits();
  if (!screenshotDirectory) return;
  const clip = browser
    ? await evaluate(`(() => {
      const { x, y, width, height } = document.querySelector(${J(DIALOG)}).getBoundingClientRect();
      return { x, y, width, height, scale: 1 };
    })()`)
    : undefined;
  const { data } = await cdp('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(join(screenshotDirectory, filename), Buffer.from(data, 'base64'), { flag: 'wx' });
}

async function openSettings() {
  if (await visible(MANAGE)) return;
  await click('button[aria-label="Settings"]');
  await waitFor(() => visible(MANAGE), 'the settings panel');
}

async function closeSettings() {
  if (await visible(MANAGE)) await click('button[aria-label="Close settings"]');
  await waitFor(async () => !(await visible(MANAGE)), 'the settings panel to close');
}

// `via` is the control the user would press: the first-use/recovery button or Settings → Manage.
async function openManager(via = 'settings') {
  if (via === 'setup') {
    await click(SETUP_TRIGGER);
  } else {
    await openSettings();
    await click(MANAGE);
  }
  await waitFor(
    () => evaluate(`document.querySelector(${J(DIALOG)})?.contains(document.activeElement)`),
    'focus inside the connection manager',
  );
  await waitFor(
    () => evaluate(`!!document.querySelector(${J(`${DIALOG} :is(.connection-summary, .error)`)})`),
    'the connection manager to load',
  );
  await assertFits();
}

// `how` varies the keyboard and pointer paths; focus must return to the control that opened the manager.
async function closeManager(how, returnTo) {
  if (how === 'escape') await press('Escape');
  else if (how === 'title') await click(`${DIALOG} button[aria-label="Close connection settings"]`);
  else await click(`${DIALOG} button`, 'Close');
  await waitFor(() => evaluate(`!document.querySelector(${J(DIALOG)})`), 'the manager to close');
  await focusIs(returnTo, 'the control that opened the manager');
}

async function setView(layout, theme) {
  await openSettings();
  await choose('display-mode', layout);
  await openSettings(); // Pointer selection of a different layout closes Settings.
  await waitFor(async () => (await checked('display-mode')) === layout, `${layout} layout`);
  await choose('theme', theme);
  await waitFor(
    async () =>
      (await checked('theme')) === theme &&
      (theme === 'system' || (await evaluate(`document.documentElement.dataset.theme`)) === theme),
    `${theme} theme`,
  );
}

// Cover both window geometries and both explicit appearances without replaying every
// transport edit in four identical forms.
async function eachView(check) {
  for (const [layout, theme, closer] of [
    ['compact', 'light', 'title'],
    ['expanded', 'dark', 'escape'],
  ]) {
    await setView(layout, theme);
    await openManager();
    await check();
    await closeManager(closer, MANAGER_TRIGGER);
  }
  await setView('expanded', 'system');
  await closeSettings();
}

async function chooseExternal() {
  await choose('client-location', 'remote');
  if (!(await evaluate(`document.querySelector(${J(`${DIALOG} details`)}).open`))) {
    await click(`${DIALOG} summary`, 'Advanced');
  }
  await choose('remote-method', 'external');
  await waitFor(async () => (await checked('remote-method')) === 'external', 'the External choice');
}

async function assertInvalid(message, selector) {
  await dialogShows(message);
  await focusIs(`${selector}[aria-invalid="true"]`, 'the invalid field');
  await assertFits();
}

async function tabWrap() {
  const footer = `${DIALOG} .form-actions:not(.check-actions) > button:last-child`;
  for (let step = 0; step < 40 && !(await active(footer)); step++) await press('Tab');
  ok(await active(footer), 'Tab did not reach the last dialog control');
  await press('Tab');
  await focusIs(`${DIALOG} button[aria-label="Close connection settings"]`, 'the first dialog control');
  await press('Tab', true);
  await focusIs(footer, 'the last dialog control');
}

const startCheck = () => click(CHECK, 'Check connection');
const checkReady = () => visible(`${CHECK}:enabled:not([aria-disabled=true])`);
const status = () => evaluate(`document.querySelector(${J(CHECK_STATUS)})?.textContent ?? null`);
const checkShows = (copy) =>
  waitFor(async () => (await status())?.startsWith(copy), `Managed check status "${copy}"`);

async function fontMetrics(pixels) {
  // Changes CSS rem metrics only; it is not native DPI or browser zoom.
  await evaluate(`document.documentElement.style.fontSize = ${J(`${pixels}px`)}`);
  ok(
    await evaluate(`getComputedStyle(document.documentElement).fontSize === ${J(`${pixels}px`)}`),
    'The requested fixture font metrics did not apply',
  );
}

async function assertCheckWraps() {
  await assertFits();
  ok(
    await evaluate(`(() => {
      const status = document.querySelector(${J(CHECK_STATUS)});
      const box = status.getBoundingClientRect();
      const text = new Range();
      text.selectNodeContents(status);
      const lines = [...text.getClientRects()];
      return lines.length > 1 && lines.every(line => line.left >= box.left - 1 && line.right <= box.right + 1);
    })()`),
    'Long diagnostic feedback did not wrap within its status region',
  );
}

async function tabTo(selector) {
  for (let step = 0; step < 40 && !(await active(selector)); step++) await press('Tab');
  ok(await active(selector), 'Tab did not reach the Managed check control');
}

async function structured(host, username, port) {
  await click(SSH_ENTRY, 'Use server and username');
  await typeInto(SSH_HOST, host);
  await typeInto(SSH_USER, username);
  await typeInto(SSH_PORT, String(port ?? ''));
}

async function hudShows(name, hp) {
  await waitFor(
    () =>
      evaluate(`(() => {
        const name = document.querySelector('.identity > span:last-child, .compact-name');
        return name?.textContent === ${J(name)} &&
          !!document.querySelector(${J(`.vital [aria-label="HP: ${hp} of 100"]`)}) &&
          !!document.querySelector('.indicator.up');
      })()`),
    'the running HUD character and fresh vitals',
  );
}

async function reloadPage() {
  await cdp('Page.enable');
  const loaded = new Promise((resolve) => {
    function onLoad({ data }) {
      if (JSON.parse(data).method !== 'Page.loadEventFired') return;
      socket.removeEventListener('message', onLoad);
      resolve();
    }
    socket.addEventListener('message', onLoad);
  });
  await cdp('Page.reload');
  await loaded;
}

async function closeBrowser() {
  // The exact nonce page has already been verified; close only its disposable browser.
  const response = await fetch(`${endpoint}/json/version`, { signal: AbortSignal.timeout(1000) });
  const { webSocketDebuggerUrl } = await response.json();
  assert.equal(new URL(webSocketDebuggerUrl).host, new URL(endpoint).host);
  const browserSocket = new WebSocket(webSocketDebuggerUrl);
  const closed = new Promise((resolve) => {
    browserSocket.addEventListener(
      'open',
      () => {
        browserSocket.send(JSON.stringify({ id: 1, method: 'Browser.close' }));
      },
      { once: true },
    );
    browserSocket.addEventListener('close', resolve, { once: true });
    browserSocket.addEventListener('error', resolve, { once: true });
  });
  await Promise.race([closed, delay(3000, undefined, { ref: false })]);
  browserSocket.close();
}

async function browserManaged() {
  const probe = (expression) => evaluate(`window.managedCheckFixture.${expression}`);
  const finish = (result) => probe(`checks.at(-1).resolve(${J(result)})`);
  const action = '.action-btn';
  const initial = 'Browser Active Fixture';
  await hudShows(initial, 67);
  await setView('expanded', 'dark');
  await openManager();
  await summaryShows('In use', 'Server login', 'imp-browser-fixture');
  ok(
    await evaluate(`(() => {
      const check = document.querySelector(${J(CHECK)});
      const help = document.getElementById(check?.getAttribute('aria-describedby'));
      return !!help?.textContent.trim();
    })()`),
    'The Check button has no accessible help',
  );
  ok((await probe('checks.length')) === 0, 'Opening Managed automatically invoked a check');
  await typeInto(SSH, '-invalid');
  await startCheck();
  await focusIs(`${SSH}[aria-invalid="true"]`, 'the invalid SSH target field');
  ok(
    await evaluate(`(() => {
      const field = document.querySelector(${J(SSH)});
      return field.getAttribute('aria-describedby')?.split(/\\s+/).includes('connection-field-error')
        && !!document.getElementById('connection-field-error')?.textContent.trim();
    })()`),
    'The invalid SSH target does not have an accessible error description',
  );
  await assertFits();
  ok((await probe('checks.length')) === 0, 'An invalid draft crossed the IPC boundary');
  await closeManager('escape', MANAGER_TRIGGER);
  await closeSettings();

  // Pending action belongs to the running HUD and must survive a diagnostic result.
  await click(action, 'Synthetic action');
  await visibleText('Forwarding “Synthetic action”…');
  await openManager();
  await tabTo(CHECK);
  await press('Enter');
  await checkShows(CHECK_COPY.progress);
  const request = await probe('checks[0].args');
  ok(
    Number.isSafeInteger(request?.requestId) &&
      request.requestId > 0 &&
      isDeepStrictEqual(request.destination, { sshTarget: 'imp-browser-fixture' }),
    'Keyboard activation did not cross IPC with the alias and request ID',
  );
  await tabTo(CHECK);
  await press('Enter');
  ok((await probe('checks.length')) === 1, 'A pending check was invoked again');
  await finish('live');
  await checkShows(CHECK_COPY.live);
  await closeManager('escape', MANAGER_TRIGGER);
  await closeSettings();
  await visibleText('Forwarding “Synthetic action”…');
  ok(
    await evaluate(`document.querySelector(${J(action)}).disabled`),
    'Checking released a pending HUD action',
  );
  await probe('finishAction()');
  await visibleText('Forwarded to the game client.');
  await probe('publish(68, 2)');
  await hudShows(initial, 68);

  // Simulated native commands stop only when explicitly released by this driver.
  await openManager();
  await tabTo(CHECK);
  await press('Enter');
  await checkShows(CHECK_COPY.progress);
  const next = await probe('checks.at(-1).args.requestId');
  ok(next > request.requestId, 'The check request ID did not increase');
  await press('Tab');
  await press(' ');
  await checkShows('Cancelling check…');
  await focusIs(CHECK, 'Check connection after cancel');
  ok(
    await probe(`cancellations.at(-1).requestId === ${J(next)}`),
    'Cancel did not address the active request',
  );
  await finish('cancelled');
  await checkShows(CHECK_COPY.cancelled);
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  const edited = await probe('checks.at(-1).args.requestId');
  await structured('192.0.2.7', 'fixture-user', 2222);
  await checkShows('Finishing previous check…');
  ok(
    await probe(`cancellations.at(-1).requestId === ${J(edited)}`),
    'Changing representation did not cancel the old check',
  );
  await finish('live');
  await waitFor(checkReady, 'the edited server login to become checkable');
  ok((await status()) === '', 'An obsolete result appeared for the edited draft');
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  await tabTo(CHECK);
  await press('Tab');
  await press(' ');
  await checkShows('Cancelling check…');
  await finish('live'); // An ordinary result racing explicit Cancel is not a cancellation acknowledgement.
  await waitFor(checkReady, 'a checkable draft after discarding the racing result');
  ok((await status()) === '', 'The racing result appeared after explicit Cancel');
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  const closing = await probe('checks.at(-1).args');
  ok(
    isDeepStrictEqual(closing.destination, {
      sshDestination: { host: '192.0.2.7', username: 'fixture-user', port: 2222 },
    }),
    'Structured Check did not send the unsaved server login',
  );
  await closeManager('escape', MANAGER_TRIGGER);
  ok(
    await probe(`cancellations.at(-1).requestId === ${J(closing.requestId)}`),
    'Escape did not cancel the active request',
  );
  await openManager();
  await checkShows('Finishing previous check…');
  await finish('down');
  await waitFor(checkReady, 'the reopened dialog to become checkable');
  ok(
    (await status()) === '' && (await value(SSH)) === 'imp-browser-fixture',
    'Closing persisted a draft or leaked an old result',
  );
  await closeManager('footer', MANAGER_TRIGGER);

  for (const pixels of [16, 20]) {
    await fontMetrics(pixels);
    for (const layout of ['compact', 'expanded']) {
      for (const theme of ['light', 'dark']) {
        await setView(layout, theme);
        await openManager();
        await structured('192.0.2.7', 'fixture-user', 2222);
        await waitFor(checkReady, 'a checkable browser server login');
        await assertFits();
        const prefix = `browser-managed-${layout}-${theme}-${pixels}`;
        if (pixels === 16 && layout === 'compact' && theme === 'light')
          await screenshot(`${prefix}-ready.png`);
        await startCheck();
        await checkShows(CHECK_COPY.progress);
        await assertFits();
        if (pixels === 16 && layout === 'compact' && theme === 'light')
          await screenshot(`${prefix}-progress.png`);
        const live = layout === 'expanded' && theme === 'dark';
        const noState = layout === 'compact' && theme === 'dark';
        await finish(live ? 'live' : noState ? 'no_usable_state' : 'forwarding_unavailable');
        await checkShows(live ? CHECK_COPY.live : noState ? CHECK_COPY.noState : CHECK_COPY.failure);
        await assertFits();
        if (!live && !noState) await assertCheckWraps();
        if (pixels === 20 && theme === 'dark') await screenshot(`${prefix}-${live ? 'live' : 'no-live'}.png`);
        if (pixels === 20 && layout === 'expanded' && theme === 'light')
          await screenshot(`${prefix}-failure.png`);
        await tabWrap();
        await closeManager('footer', MANAGER_TRIGGER);
      }
    }
  }
  const restartRequired =
    'Imp could not confirm that a connection check stopped. Quit and reopen Imp before checking or saving connections.';
  await openManager();
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  await tabTo(CHECK);
  await press('Tab');
  await press(' ');
  await checkShows('Cancelling check…');
  await finish('retirement_unconfirmed');
  await dialogShows(restartRequired);
  await assertFits();
  await screenshot('browser-managed-expanded-dark-20-restart-required.png');
  ok((await status()) === '', 'Unconfirmed retirement appeared as an ordinary check result');
  await closeManager('footer', MANAGER_TRIGGER);
  await visibleText(restartRequired);
  await openManager();
  await typeInto(SSH, 'imp-browser-alternate');
  await dialogShows(restartRequired);
  ok(!(await checkReady()) && (await saveDisabled()), 'Unconfirmed retirement allowed check or save');
  await closeManager('footer', MANAGER_TRIGGER);
  await closeSettings();
  await hudShows(initial, 68);
  ok(
    await evaluate(`(() => {
      const { saves, actions, sockets, closed } = window.managedCheckFixture;
      return saves === 0 && actions.length === 1 && actions[0].command === 'fixture_action' &&
        actions[0].context.session === 'browser_fixture' &&
        JSON.stringify(sockets) === '["/state","/action"]' && JSON.stringify(closed) === '["/action"]';
    })()`),
    'A check saved settings, submitted another action, or replaced the running consumer',
  );
}

async function installedManagedDiagnostic() {
  const { host, username, port, alias, runningName, runningHp, diagnosticName } = JSON.parse(
    readFileSync(join(diagnosticFixture, 'manifest.json'), 'utf8'),
  );
  const eventLog = join(diagnosticFixture, 'events.log');
  const events = () => readFileSync(eventLog, 'utf8').split(/\r?\n/);
  const count = (event) => events().filter((line) => line === event).length;
  const traffic = (event, expected) =>
    waitFor(() => count(event) === expected, 'the expected installed fixture traffic');
  async function command(name) {
    const ack = `COMMAND ${name}`;
    const before = count(ack);
    appendFileSync(join(diagnosticFixture, 'commands.log'), `${name}\n`);
    await traffic(ack, before + 1);
  }
  async function observeSsh(check) {
    // B is held until PowerShell acquires a handle to this desktop's exact SSH child.
    const request = join(diagnosticFixture, `ssh-check-${check}`);
    writeFileSync(request, '');
    await waitFor(() => existsSync(`${request}.observed`), 'the held SSH child to be observed');
    return () => waitFor(() => existsSync(`${request}.retired`), 'the exact SSH child to retire');
  }
  const before = readFileSync(tunnelConfig);
  const expectedRunning = config().sshTarget;
  const assertUnchanged = async () => {
    await summaryShows('In use', 'Server login', expectedRunning);
    await dialogLacks(PENDING);
    await dialogLacks(diagnosticName);
    ok(readFileSync(tunnelConfig).equals(before), 'The diagnostic changed saved connection bytes');
    ok(
      count('A OPEN /state') === 1 && count('A CLOSED /state') === 0,
      'The diagnostic replaced the active consumer',
    );
  };
  // Both real relay backends must have retained live data and a registered, idle action consumer.
  for (const backend of ['A', 'B']) {
    await traffic(`${backend} FIXTURE OPEN /ingest`, 1);
    await traffic(`${backend} FIXTURE OPEN /action-consumer`, 1);
    await traffic(`${backend} FIXTURE READY live retained=2`, 1);
  }
  await traffic('A OPEN /state', 1);
  await setView('expanded', 'dark');
  await closeSettings();
  await hudShows(runningName, runningHp);
  const feedback = () => evaluate(`document.querySelector('.action-feedback-region')?.textContent.trim()`);
  ok((await feedback()) === '', 'The installed HUD did not start with idle action feedback');
  await openManager();
  await summaryShows('In use', 'Server login', expectedRunning);
  ok(count('B OPEN /state') === 0, 'Opening Managed contacted the draft backend');
  await typeInto(SSH, alias);
  await waitFor(checkReady, 'the installed alias draft check control');
  await screenshot('managed-check-ready.png');
  await tabTo(CHECK);
  await press('Enter');
  await checkShows(CHECK_COPY.progress);
  await traffic('B OPEN /state', 1);
  const liveRetired = await observeSsh(1);
  await screenshot('managed-check-progress.png');
  await command('update');
  await traffic('A UPDATED', 1);
  await command('live');
  await checkShows(CHECK_COPY.live);
  await traffic('B CLOSED /state', 1);
  await liveRetired();
  await screenshot('managed-check-live.png');
  await assertUnchanged();
  await closeManager('escape', MANAGER_TRIGGER);
  await closeSettings();
  await hudShows(runningName, runningHp + 1);
  await bodyLacks(diagnosticName);
  ok((await feedback()) === '', 'Checking changed installed HUD action feedback');

  await fontMetrics(20);
  await setView('compact', 'light');
  await openManager();
  await structured(host, username, port);
  // The real relay captures startup status when B subscribes, not when its held writer is released.
  await command('down');
  await command('hold');
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  await traffic('B OPEN /state', 2);
  const downRetired = await observeSsh(2);
  await command('update');
  await traffic('A UPDATED', 2);
  await command('release');
  await checkShows(CHECK_COPY.down);
  await downRetired();
  await traffic('B CLOSED /state', 2);
  await assertUnchanged();
  await assertCheckWraps();
  await screenshot('managed-check-no-live.png');
  await tabWrap();
  await command('hold');
  await startCheck();
  await checkShows(CHECK_COPY.progress);
  await traffic('B OPEN /state', 3);
  const cancelledRetired = await observeSsh(3);
  await command('update');
  await traffic('A UPDATED', 3);
  await tabTo(CHECK);
  await press('Tab');
  await press(' ');
  await checkShows(CHECK_COPY.cancelled);
  await traffic('B CLOSED /state', 3);
  await cancelledRetired();
  await assertUnchanged();
  await focusIs(CHECK, 'Check connection after cancel');

  await fontMetrics(16);
  await typeInto(SSH_PORT, fixturePort);
  await startCheck();
  await checkShows(CHECK_COPY.unreachable);
  await assertUnchanged();
  await assertCheckWraps();
  await screenshot('managed-check-failure.png');
  await closeManager('footer', MANAGER_TRIGGER);
  await closeSettings();
  await hudShows(runningName, runningHp + 3);
  await command('update');
  await hudShows(runningName, runningHp + 4);
  await bodyLacks(diagnosticName);
  await click('button[aria-label="Actions"]');
  ok((await feedback()) === '', 'Checking changed installed HUD action feedback');
  await click('button[aria-label="Close actions"]');
  ok(readFileSync(tunnelConfig).equals(before), 'The diagnostic changed saved connection bytes');
  ok(
    count('A OPEN /state') === 1 &&
      count('A CLOSED /state') === 0 &&
      count('B OPEN /state') === 3 &&
      count('B CLOSED /state') === 3,
    'The diagnostic replaced the active consumer or left a temporary consumer open',
  );
  ok(
    !events().some((line) =>
      /^[AB] (?:DISPATCH\b|REQUEST\b|MESSAGE\b|(?:OPEN|CLOSED) (?!\/state$))/.test(line),
    ),
    'The installed diagnostic sent a client message, used an unexpected route, or dispatched an action',
  );
}

const phases = {
  async first() {
    await openManager('setup');
    await dialogShows('No connection configured');
    await dialogLacks(PENDING);
    // No route is preselected, so nothing can be saved by accident.
    ok((await checked('client-location')) === null, 'A client location was preselected');
    ok(!(await visible(`${DIALOG} input[name="remote-method"]`)), 'Remote methods shown before a choice');
    ok(!(await visible(CHECK)), 'An unset route offered a Managed check');
    ok(await saveDisabled(), 'Save was enabled with no route chosen');
    await focusIs(`${DIALOG} input[name="client-location"][value="local"]`, 'first location choice');
    await press(' '); // Keyboard selection of the focused radio.
    await waitFor(async () => (await checked('client-location')) === 'local', 'keyboard selection');
    await noFields();
    ok(!(await visible(CHECK)), 'Local offered a Managed check');
    await choose('client-location', 'remote');
    ok((await checked('remote-method')) === null, 'A remote method was preselected');
    ok(await saveDisabled(), 'Save was enabled with no remote method chosen');
    await noFields();
    ok(!(await evaluate(`document.querySelector(${J(`${DIALOG} details`)}).open`)), 'Advanced started open');

    await choose('remote-method', 'managed');
    await click(SSH_HOST);
    await press('Enter');
    await assertInvalid('Enter a server hostname or IP address.', SSH_HOST);
    await click(SSH_ENTRY, 'Use an existing SSH alias');
    await click(SSH);
    await press('Enter');
    await assertInvalid('Enter your SSH target.', SSH);
    await typeInto(SSH, 'imp-fixture');
    await click(SSH_ENTRY, 'Use server and username');
    await waitFor(() => visible(SSH_HOST), 'the server address field');
    await choose('remote-method', 'direct');
    ok(!(await visible(CHECK)), 'Direct offered a Managed check');
    ok((await value(URL_FIELD)) === '' && (await value(TOKEN)) === '', 'Direct fields were prefilled');
    await typeInto(URL_FIELD, 'https://127.0.0.1/state');
    await press('Enter');
    await assertInvalid('Enter a WSS state URL ending in /state', URL_FIELD);
    await typeInto(URL_FIELD, 'wss://127.0.0.1/state');
    await press('Enter');
    await assertInvalid('Enter the pairing token for this endpoint.', TOKEN);
    await typeInto(TOKEN, 'short');
    await press('Enter');
    await assertInvalid('Enter a valid 43-character pairing token', TOKEN);
    await typeInto(TOKEN, '');
    await chooseExternal();
    ok((await checked('remote-method')) === 'external', 'External could not be chosen');
    await click(`${DIALOG} summary`, 'Advanced'); // Collapsing Advanced withdraws External.
    await waitFor(saveDisabled, 'Save to be disabled after collapsing Advanced');
    ok((await checked('remote-method')) === null, 'External stayed selected after collapsing Advanced');
    await tabWrap();
    await closeManager('footer', SETUP_TRIGGER);
    ok(!existsSync(tunnelConfig), 'Exploring the manager wrote settings');

    await click('button', 'Not now');
    await visibleText(DEFERRED_HINT);
    await eachView(async () => {
      await dialogShows('No connection configured');
      ok((await checked('client-location')) === null, 'A client location was preselected');
      await choose('client-location', 'remote');
      await choose('remote-method', 'direct');
      await assertFits();
      await typeInto(URL_FIELD, 'wss://127.0.0.1/state');
      await save();
      await assertInvalid('Enter the pairing token for this endpoint.', TOKEN);
      ok(!existsSync(tunnelConfig), 'A rejected draft wrote settings');
    });
  },

  async deferred() {
    await visibleText(DEFERRED_HINT); // Dismissal persisted.
    ok(!existsSync(tunnelConfig), 'Deferral wrote settings');
    await openManager();
    await dialogShows('No connection configured');
    await choose('client-location', 'local');
    await noFields(); // Local needs no endpoint or credential.
    ok(!(await saveDisabled()), 'Local could not be saved');
    await save();
    await dialogShows(PENDING);
    await dialogShows(SAVED_PENDING);
    await dialogShows('No connection configured'); // Nothing is running yet.
    ok(configIs({ mode: 'local' }), 'Local was not saved');
    await assertNoSecrets();
    await closeManager('escape', MANAGER_TRIGGER);
    await openManager();
    await dialogShows(PENDING); // The pending save survives reopening.
    ok((await checked('client-location')) === 'local', 'Saved Local was not shown');
    await closeManager('footer', MANAGER_TRIGGER);
  },

  async local() {
    await eachView(async () => {
      await dialogShows('This computer');
      await dialogShows('In use');
      await dialogLacks(PENDING);
      ok((await checked('client-location')) === 'local', 'Running Local was not selected');
      await noFields();
    });
    await openManager();
    await save(); // Unchanged.
    await dialogShows(SAVED);
    await dialogLacks(PENDING);
    ok(configIs({ mode: 'local' }), 'An unchanged save altered settings');
    await chooseExternal(); // Mode change.
    await save();
    await waitFor(async () => {
      ok(
        !(await visible(`${DIALOG} .error`)),
        'Native save returned an error on the Local to External change',
      );
      return config().mode === 'external';
    }, 'the persisted mode to change from Local to External');
    await dialogShows(PENDING);
    await dialogShows('Own SSH tunnel');
    ok(config().mode === 'external', 'External was not saved');
    await closeManager('escape', MANAGER_TRIGGER);
  },

  async managed() {
    const seeded = config();
    const alternate = seeded.sshTarget.replace('imp-fixture@', 'imp-alternate@');
    ok(alternate !== seeded.sshTarget, 'The Managed fixture has an unexpected target');
    await eachView(async () => {
      await dialogShows('Server login');
      await dialogShows(seeded.sshTarget);
      ok((await checked('remote-method')) === 'managed', 'Managed was not selected');
      ok((await value(SSH)) === seeded.sshTarget, 'The saved target was not shown');
      await typeInto(SSH, '');
      await save();
      await assertInvalid('Enter your SSH target.', SSH);
      ok(configIs(seeded), 'A rejected target changed settings');
    });
    await openManager();
    await screenshot('managed-ssh-alias.png');
    await save(); // Unchanged.
    await dialogShows(SAVED);
    await dialogLacks(PENDING);
    ok(configIs(seeded), 'An unchanged save altered settings');
    await typeInto(SSH, alternate); // Target change; the running target stays visible.
    await save();
    await dialogShows(PENDING);
    await dialogShows(alternate);
    await dialogShows(seeded.sshTarget);
    ok(configIs({ mode: 'managed', sshTarget: alternate }), 'The target was not saved');
    await closeManager('footer', MANAGER_TRIGGER);
  },

  async 'managed-structured'() {
    const before = rawConfig();
    await openManager();
    await choose('client-location', 'remote');
    await choose('remote-method', 'managed');
    await typeInto(SSH_HOST, DESTINATION.host);
    await typeInto(SSH_USER, DESTINATION.username);
    await typeInto(SSH_PORT, String(DESTINATION.port));
    await closeManager('escape', MANAGER_TRIGGER);
    ok(bytesAre(before), 'Closing an unsaved server login changed settings');

    await openManager();
    await summaryShows('In use', 'This computer');
    await dialogLacks(PENDING);
    ok((await checked('client-location')) === 'local', 'The cancelled server login was retained');
    await choose('client-location', 'remote');
    await choose('remote-method', 'managed');
    await typeInto(SSH_HOST, DESTINATION.host);
    await save();
    await assertInvalid('Enter a valid SSH username.', SSH_USER);
    await typeInto(SSH_USER, DESTINATION.username);
    await typeInto(SSH_PORT, '0');
    await save();
    await assertInvalid('Enter a port from 1 to 65535.', SSH_PORT);
    ok(bytesAre(before), 'Rejected server login settings changed the file');
    await typeInto(SSH_PORT, String(DESTINATION.port));
    await save();
    await dialogShows(SAVED_PENDING);
    await summaryShows('In use', 'This computer');
    await summaryShows(PENDING, 'Server login', DESTINATION_LABEL);
    ok(configIs({ mode: 'managed', sshDestination: DESTINATION }), 'Server login was not saved');
    ok(!Object.hasOwn(config(), 'sshTarget'), 'Server login was saved with a legacy SSH target');
    await closeManager('footer', MANAGER_TRIGGER);
  },

  async 'managed-restart'() {
    const before = rawConfig();
    ok(
      configIs({ mode: 'managed', sshDestination: DESTINATION }),
      'The saved server login did not survive restart',
    );
    const check = async () => {
      await summaryShows('In use', 'Server login', DESTINATION_LABEL);
      await dialogLacks(PENDING);
      ok((await checked('remote-method')) === 'managed', 'Managed was not selected after restart');
      ok((await value(SSH_HOST)) === DESTINATION.host, 'The saved server was not shown');
      ok((await value(SSH_USER)) === DESTINATION.username, 'The saved username was not shown');
      ok((await value(SSH_PORT)) === String(DESTINATION.port), 'The saved port was not shown');
      ok(!(await visible(SSH)), 'The alias field was shown for a structured server login');
    };
    await eachView(check);
    await openManager();
    await check();
    await screenshot('managed-ssh-server-login.png');
    await closeManager('footer', MANAGER_TRIGGER);
    ok(bytesAre(before), 'Opening a saved server login rewrote it');
  },

  async external() {
    await eachView(async () => {
      await dialogShows('Own SSH tunnel');
      await dialogLacks(PENDING);
      ok((await checked('client-location')) === 'remote', 'Remote was not selected');
      ok((await checked('remote-method')) === 'external', 'External was not selected');
      ok(!(await visible(CHECK)), 'External offered a Managed check');
      ok(await evaluate(`document.querySelector(${J(`${DIALOG} details`)}).open`), 'Advanced was closed');
      await noFields();
    });
  },

  async direct() {
    const seeded = config();
    const moved = new URL(seeded.remoteUrl);
    moved.pathname = '/alternate/state'; // Same reserved socket, different path.
    const alternate = moved.href;
    const secrets = [seeded.pairingToken, TOKEN_A, TOKEN_B];
    await eachView(async () => {
      await dialogShows('Remote Imp credentials');
      ok((await checked('remote-method')) === 'direct', 'Direct was not selected');
      ok((await value(URL_FIELD)) === seeded.remoteUrl, 'The saved endpoint was not shown');
      ok((await value(TOKEN)) === '', 'The saved credential was shown');
      await dialogShows('Leave the token blank to keep your saved credential.');
      await assertNoSecrets(secrets);
      await choose('client-location', 'local'); // Removal needs confirmation.
      await save();
      await dialogShows('Remove credentials and save');
      await assertFits();
      await cancel();
      await focusIs(`${DIALOG} button[type="submit"]`, 'Save after cancelling');
      ok(configIs(seeded), 'Cancelling removal changed settings');
    });
    await openManager();
    await save(); // Blank token on the same endpoint keeps the credential.
    await dialogShows(SAVED);
    await dialogLacks(PENDING);
    ok(configIs(seeded), 'A blank-token save altered the credential');
    await assertNoSecrets(secrets);

    await typeInto(TOKEN, TOKEN_A); // Token-only change.
    await assertNoSecrets(secrets, true);
    await save();
    await dialogShows(PENDING);
    await dialogShows('A different pairing token is saved.');
    ok(configIs({ ...seeded, pairingToken: TOKEN_A }), 'The token was not replaced');
    await assertNoSecrets(secrets);

    // A changed endpoint is bound to a new token; without one the save is rejected untouched.
    await typeInto(URL_FIELD, alternate);
    await dialogShows('Enter the pairing token for this endpoint.');
    const before = rawConfig();
    await save();
    await assertInvalid('Enter the pairing token for this endpoint.', TOKEN);
    ok(bytesAre(before), 'A rejected endpoint change altered settings');
    await typeInto(TOKEN, TOKEN_B); // Endpoint change.
    await save();
    await dialogShows(PENDING);
    await dialogShows(alternate);
    await dialogShows(seeded.remoteUrl);
    ok(
      configIs({ mode: 'direct', remoteUrl: alternate, pairingToken: TOKEN_B }),
      'The endpoint was not saved',
    );
    await assertNoSecrets(secrets);

    await choose('client-location', 'local'); // Mode change; cancel keeps, confirm removes.
    await save();
    await cancel();
    await focusIs(`${DIALOG} button[type="submit"]`, 'Save after cancelling');
    ok(
      configIs({ mode: 'direct', remoteUrl: alternate, pairingToken: TOKEN_B }),
      'Cancelling removal changed settings',
    );
    await save();
    await click(`${DIALOG} button`, 'Remove credentials and save');
    await dialogShows(SAVED_PENDING);
    await dialogShows(PENDING);
    const removed = config();
    ok(
      removed.mode === 'local' && !removed.pairingToken && !removed.remoteUrl,
      'Credentials were not removed',
    );
    await assertNoSecrets(secrets);
    await closeManager('footer', MANAGER_TRIGGER);
  },

  async legacy() {
    // A legacy empty file is an existing External route, not first use.
    const before = rawConfig();
    await bodyLacks('Connection not set up');
    ok(!(await visible(SETUP_TRIGGER)), 'A first-use prompt was offered for an existing route');
    await eachView(async () => {
      await dialogShows('Own SSH tunnel');
      await dialogLacks('No connection configured');
      await dialogLacks(PENDING);
      ok((await checked('remote-method')) === 'external', 'External was not selected');
      await noFields();
    });
    ok(bytesAre(before), 'Opening a legacy file rewrote it');
  },

  async invalid() {
    await bodyLacks('Connection not set up');
    const before = rawConfig();
    await visibleText('Connection settings unavailable');
    await openManager('setup');
    await dialogShows('Imp could not read tunnel.json');
    await dialogLacks('Where does your MUD client run?');
    ok(!(await visible(`${DIALOG} :is(input, button[type="submit"])`)), 'Unreadable settings offer controls');
    await closeManager('escape', SETUP_TRIGGER);
    await eachView(async () => {
      await dialogShows('Imp could not read tunnel.json');
      ok(
        !(await visible(`${DIALOG} :is(input, button[type="submit"])`)),
        'Unreadable settings offer controls',
      );
    });
    ok(bytesAre(before), 'Unreadable settings were overwritten');
  },

  'managed-diagnostic': installedManagedDiagnostic,
  'browser-managed': browserManaged,
};

const environment = browser ? 'Controlled Chromium' : 'Installed';
try {
  if (!Object.hasOwn(phases, phase)) throw new Error(`Unknown phase ${phase}`);
  let socketAttempts = 0;
  const inactive = ['first', 'deferred', 'invalid'].includes(phase);
  if (browser) {
    await cdp('Page.addScriptToEvaluateOnNewDocument', { source: `(${setupManagedCheckBrowser})()` });
    await reloadPage();
  } else if (inactive) {
    // Attach before a fresh renderer boot so even an immediately rejected consumer is observable.
    socket.addEventListener('message', ({ data }) => {
      if (JSON.parse(data).method === 'Network.webSocketCreated') socketAttempts++;
    });
    await cdp('Network.enable');
    await reloadPage();
  } else {
    await waitFor(() => visible('button[aria-label="Settings"]'), 'the configured HUD');
    ok(!(await visible(SETUP_TRIGGER)), 'A configured desktop offered first-use setup');
  }
  await phases[phase]();
  if (inactive) ok(socketAttempts === 0, 'An inactive desktop attempted a consumer or action connection');
  process.stdout.write(`${environment} connection UI phase ${phase} passed.\n`);
} catch (error) {
  // Messages are fixed strings, but redact token- and digest-shaped runs in case a CDP error carries one.
  const message = String(error?.message).replace(/[A-Za-z0-9_-]{40,}/g, '[redacted]');
  console.error(`${environment} connection UI phase ${phase} failed: ${message}`);
  if (phase === 'managed-diagnostic') {
    const text = await status().catch(() => null);
    const category = Object.entries(CHECK_COPY).find(([, prefix]) => text?.startsWith(prefix))?.[0];
    console.error(`Installed Managed status: ${category ?? (text ? 'other' : 'empty')}`);
  }
  // Allowlisted booleans/enums only. Never print the form, configuration object or native error.
  const state = await evaluate(`(() => {
    const d = document.querySelector('.connection-dialog');
    const selected = name => d?.querySelector('input[name="' + name + '"]:checked')?.value;
    return { open: !!d, local: selected('client-location') === 'local',
      external: selected('remote-method') === 'external', submitEnabled: !!d?.querySelector('button[type=submit]:enabled'),
      error: !!d?.querySelector('.error'), saved: !!d?.querySelector('.saved-message'),
      pending: !!d?.innerText.includes('Saved for next launch') };
  })()`).catch(() => null);
  console.error('Installed form state:', state);
  process.exitCode = 1;
} finally {
  if (browser) await closeBrowser().catch(() => {});
  socket.close();
}
