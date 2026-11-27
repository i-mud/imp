export function waitForDomTransition(ready: () => boolean, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (ready()) return Promise.resolve();
  const { promise, resolve, reject } = Promise.withResolvers<void>();
  const observer = new MutationObserver(() => {
    if (!ready()) return;
    observer.disconnect();
    signal.removeEventListener('abort', abort);
    resolve();
  });
  function abort() {
    observer.disconnect();
    reject(signal.reason);
  }
  signal.addEventListener('abort', abort, { once: true });
  observer.observe(document.body, {
    attributes: true,
    childList: true,
    characterData: true,
    subtree: true,
  });
  return promise;
}
