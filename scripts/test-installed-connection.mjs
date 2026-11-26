import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

// The PowerShell caller verifies that this listener belongs to its isolated WebView.
const endpoint = `http://127.0.0.1:${Number(process.argv[2])}`;
const [phase, tunnelConfig] = process.argv.slice(3);
setTimeout(() => {
  console.error('Installed connection UI acceptance timed out');
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
  const { result, exceptionDetails } = await cdp('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  assert.equal(exceptionDetails, undefined, 'Reading the installed page failed');
  return result.value;
}

function visibleText(text) {
  return waitFor(() => evaluate(`document.body?.innerText.includes(${JSON.stringify(text)})`), text);
}

async function click(selector, text) {
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
const SSH = `${DIALOG} input[type="text"]`;
const URL_FIELD = `${DIALOG} input[type="url"]`;
const TOKEN = `${DIALOG} input[type="password"]`;
const MANAGE = 'button[aria-label="Manage connection"]';
const MANAGER_TRIGGER = '[data-connection-manager-trigger]';
const SETUP_TRIGGER = '[data-connection-setup-trigger]';
const SAVED = 'Connection settings saved.';
const SAVED_PENDING = 'Saved. Restart Imp to use this connection.';
const PENDING = 'Saved for next launch';
const DEFERRED_HINT = 'Open Settings → Connection → Manage to set up a connection.';
// Fixture-only synthetic credentials; each ends in a character the pairing-token format accepts.
const TOKEN_A = 'Q'.repeat(43);
const TOKEN_B = 'g'.repeat(43);

// Failure messages must never carry configuration values, so checks pass a fixed message.
const ok = (condition, message) => assert.ok(condition, message);

const dialogText = () => evaluate(`document.querySelector(${J(DIALOG)})?.innerText ?? ''`);
const dialogShows = (text) =>
  waitFor(async () => (await dialogText()).includes(text), `dialog text: ${text}`);
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
  return ['mode', 'sshTarget', 'remoteUrl', 'pairingToken'].every(
    (name) => (saved[name] ?? null) === (expected[name] ?? null),
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
  await waitFor(
    () =>
      evaluate(`(() => {
    const dialog = document.querySelector(${J(DIALOG)}).getBoundingClientRect();
    const within = (box, outer) => box.left >= outer.left - 1 && box.right <= outer.right + 1 &&
      box.top >= outer.top - 1 && box.bottom <= outer.bottom + 1;
    const viewport = {left: 0, top: 0, right: innerWidth, bottom: innerHeight};
    if (!within(dialog, viewport)) return false;
    for (const control of document.querySelectorAll(${J(DIALOG)} + ' :is(input, button, summary)')) {
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
  const footer = `${DIALOG} .form-actions > button:last-child`;
  for (let step = 0; step < 40 && !(await active(footer)); step++) await press('Tab');
  ok(await active(footer), 'Tab did not reach the last dialog control');
  await press('Tab');
  await focusIs(`${DIALOG} button[aria-label="Close connection settings"]`, 'the first dialog control');
  await press('Tab', true);
  await focusIs(footer, 'the last dialog control');
}

const phases = {
  async first() {
    await openManager('setup');
    await dialogShows('No connection configured');
    await dialogLacks(PENDING);
    // No route is preselected, so nothing can be saved by accident.
    ok((await checked('client-location')) === null, 'A client location was preselected');
    ok(!(await visible(`${DIALOG} input[name="remote-method"]`)), 'Remote methods shown before a choice');
    ok(await saveDisabled(), 'Save was enabled with no route chosen');
    await focusIs(`${DIALOG} input[name="client-location"][value="local"]`, 'first location choice');
    await press(' '); // Keyboard selection of the focused radio.
    await waitFor(async () => (await checked('client-location')) === 'local', 'keyboard selection');
    await noFields();
    await choose('client-location', 'remote');
    ok((await checked('remote-method')) === null, 'A remote method was preselected');
    ok(await saveDisabled(), 'Save was enabled with no remote method chosen');
    await noFields();
    ok(!(await evaluate(`document.querySelector(${J(`${DIALOG} details`)}).open`)), 'Advanced started open');

    await choose('remote-method', 'managed');
    await click(SSH);
    await press('Enter');
    await assertInvalid('Enter your SSH target.', SSH);
    await choose('remote-method', 'direct');
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

  async external() {
    await eachView(async () => {
      await dialogShows('Own SSH tunnel');
      await dialogLacks(PENDING);
      ok((await checked('client-location')) === 'remote', 'Remote was not selected');
      ok((await checked('remote-method')) === 'external', 'External was not selected');
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
};

try {
  if (!phases[phase]) throw new Error(`Unknown phase ${phase}`);
  let socketAttempts = 0;
  const inactive = ['first', 'deferred', 'invalid'].includes(phase);
  if (inactive) {
    // Attach before a fresh renderer boot so even an immediately rejected consumer is observable.
    socket.addEventListener('message', ({ data }) => {
      if (JSON.parse(data).method === 'Network.webSocketCreated') socketAttempts++;
    });
    await cdp('Network.enable');
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
  } else {
    await waitFor(() => visible('button[aria-label="Settings"]'), 'the configured HUD');
    ok(!(await visible(SETUP_TRIGGER)), 'A configured desktop offered first-use setup');
  }
  await phases[phase]();
  if (inactive) ok(socketAttempts === 0, 'An inactive desktop attempted a consumer or action connection');
  process.stdout.write(`Installed connection UI phase ${phase} passed.\n`);
} catch (error) {
  // Messages are fixed strings, but redact token- and digest-shaped runs in case a CDP error carries one.
  const message = String(error?.message).replace(/[A-Za-z0-9_-]{40,}/g, '[redacted]');
  console.error(`Installed connection UI phase ${phase} failed: ${message}`);
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
  socket.close();
}
