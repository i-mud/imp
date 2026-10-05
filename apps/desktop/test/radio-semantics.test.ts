import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';

import SettingsPanel from '../src/components/SettingsPanel.svelte';
import ConnectionDialog from '../src/components/ConnectionDialog.svelte';
import AlertDialog from '../src/components/AlertDialog.svelte';

describe('native segmented radio semantics', () => {
  it('labels display and theme radios and exposes the selected preferences', () => {
    const { body } = render(SettingsPanel, {
      props: {
        mode: 'compact',
        theme: 'light',
        appVersion: null,
        alertSaveError: null,
        onmodechange: () => {},
        onthemechange: () => {},
        onmanageconnection: () => {},
        onmanagealerts: () => {},
        onmanageactions: () => {},
      },
    });

    expect(body).toMatch(/role="radiogroup" aria-label="Display mode"/);
    expect(body).toMatch(
      /<label[^>]*>\s*<input type="radio" name="display-mode" value="expanded"[^>]*>\s*<span[^>]*>Expanded<\/span>/,
    );
    expect(body).toMatch(/<input type="radio" name="display-mode" value="compact" checked/);
    expect(body).toMatch(/role="radiogroup" aria-label="Theme"/);
    for (const name of ['Dark', 'Light', 'System']) {
      expect(body).toMatch(new RegExp(`<input type="radio" name="theme"[^>]*aria-label="${name}"`));
    }
    expect(body).toMatch(/<input type="radio" name="theme" value="light" checked/);
  });

  it('labels native connection choices in one named group', () => {
    const { body } = render(ConnectionDialog, {
      props: {
        settings: { mode: 'external', sshTarget: '', remoteUrl: '', hasPairingToken: false },
        loadError: null,
        saveError: null,
        onsave: async () => true,
        onclose: () => {},
      },
    });

    expect(body).toMatch(/role="radiogroup" aria-label="Connection mode"/);
    for (const name of ['External', 'Local', 'Managed', 'Direct']) {
      expect(body).toMatch(
        new RegExp(
          `<label[^>]*>\\s*<input type="radio" name="connection-mode"[^>]*>\\s*<span[^>]*>${name}<\\/span>`,
        ),
      );
    }
    expect(body).toMatch(/value="external" checked/);
  });

  it('associates alert trigger labels with native radios and selects Vitals', () => {
    const { body } = render(AlertDialog, {
      props: { definitions: [], onchange: () => true, saveError: null, onclose: () => {} },
    });

    expect(body).toMatch(/role="radiogroup" aria-label="Alert trigger"/);
    expect(body).toMatch(
      /<label[^>]*>\s*<input type="radio" name="alert-trigger" checked[^>]*>\s*<span[^>]*>Vitals<\/span>/,
    );
    expect(body).toMatch(
      /<label[^>]*>\s*<input type="radio" name="alert-trigger"[^>]*>\s*<span[^>]*>Text<\/span>/,
    );
  });
});
