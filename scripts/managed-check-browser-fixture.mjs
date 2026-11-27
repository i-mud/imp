// Injected before the app loads, only in a disposable headless Chromium profile.
// Tauri IPC and relay sockets are deliberately simulated here, never in the installed test.
export function setupManagedCheckBrowser() {
  const target = 'imp-browser-fixture';
  const running = { mode: 'managed', sshTarget: target, remoteUrl: '', hasPairingToken: false };
  const stateUrl = 'ws://127.0.0.1:8789/state';
  const context = { session: 'browser_fixture', foreground: 1, connection: 1 };
  const at = 1_758_000_000_100;
  const frame = (type, data) => ({ protocol: 2, type, ...data });
  const emit = (socket, message) =>
    socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(message) }));
  const fixture = (window.managedCheckFixture = {
    checks: [],
    cancellations: [],
    saves: 0,
    sockets: [],
    actions: [],
    closed: [],
    size: null,
    publish(hp, seq) {
      emit(
        fixture.consumer,
        frame('snapshot', {
          at,
          context,
          seq,
          state: {
            character: {
              name: 'Browser Active Fixture',
              hp: { current: hp, max: 100 },
              mana: null,
              moves: null,
            },
            target: null,
          },
        }),
      );
    },
    finishAction() {
      emit(fixture.action, frame('action-result', { status: 'forwarded', detail: null }));
    },
  });
  localStorage.setItem(
    'imp.actions',
    JSON.stringify([{ id: 'fixture', label: 'Synthetic action', command: 'fixture_action' }]),
  );
  const realSetTimeout = window.setTimeout;
  let holdActionTimeout = false;
  window.setTimeout = (callback, milliseconds, ...args) => {
    if (holdActionTimeout && milliseconds === 5_000) {
      holdActionTimeout = false;
      // The driver explicitly completes the pending action; don't race its production deadline.
      return realSetTimeout(() => {}, 2 ** 31 - 1);
    }
    return realSetTimeout(callback, milliseconds, ...args);
  };
  const RealWebSocket = window.WebSocket;
  window.WebSocket = class extends EventTarget {
    constructor(url, protocols) {
      super();
      const endpoint = new URL(url);
      if (endpoint.host === location.host && protocols === 'vite-hmr') {
        return new RealWebSocket(url, protocols);
      }
      if (endpoint.host !== '127.0.0.1:8789') throw new Error('Unexpected fixture endpoint');
      const { pathname: path } = endpoint;
      if (path === '/state') fixture.consumer = this;
      else if (path === '/action') {
        fixture.action = this;
        holdActionTimeout = true;
      } else throw new Error('Unexpected fixture endpoint');
      this.path = path;
      fixture.sockets.push(path);
      queueMicrotask(() => {
        this.dispatchEvent(new Event('open'));
        if (path === '/state') {
          emit(this, frame('hello', { at, relay: { name: 'Browser fixture', version: '1' } }));
          fixture.publish(67, 1);
          emit(this, frame('status', { at, feed: 'live', detail: null }));
        }
      });
    }
    send(message) {
      fixture.actions.push(JSON.parse(message));
    }
    close() {
      fixture.closed.push(this.path);
      this.dispatchEvent(new Event('close'));
    }
  };
  window.__TAURI_INTERNALS__ = {
    metadata: { currentWindow: { label: 'main' } },
    async invoke(command, args) {
      switch (command) {
        case 'connection_config':
          return { mode: 'local', stateUrl, authenticationToken: null };
        case 'connection_settings':
          return { running, saved: running, pendingRestart: false };
        case 'tunnel_status':
          return { diagnostic: 'live' };
        case 'plugin:app|version':
          return '0.0.0';
        case 'plugin:window|set_always_on_top':
          return;
        case 'plugin:window|set_size':
          fixture.size = JSON.parse(JSON.stringify(args.value));
          return;
        case 'check_managed_connection': {
          const { promise, resolve } = Promise.withResolvers();
          fixture.checks.push({ args, resolve });
          return promise;
        }
        case 'cancel_managed_connection':
          fixture.cancellations.push(args);
          return;
        case 'save_connection_settings':
          fixture.saves++;
          break;
      }
      throw new Error('Unexpected browser fixture command');
    },
  };
}
