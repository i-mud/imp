import assert from 'node:assert/strict';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const repoRoot = resolve(import.meta.dirname, '..');

test('updateChangelog accepts the repository placeholder and refuses handwritten notes', async () => {
  const root = mkdtempSync(join(tmpdir(), 'imp-changelog-'));

  try {
    const scripts = join(root, 'scripts');
    mkdirSync(scripts);
    copyFileSync(resolve(repoRoot, 'scripts/release-notes.mjs'), join(scripts, 'release-notes.mjs'));
    const modulePath = join(scripts, 'semantic-release-prepare.mjs');
    // Expose only the temporary copy's updater; never invoke prepare or its Git commands.
    writeFileSync(
      modulePath,
      readFileSync(resolve(repoRoot, 'scripts/semantic-release-prepare.mjs'), 'utf8') +
        '\nexport { updateChangelog };\n',
    );
    const { updateChangelog } = await import(pathToFileURL(modulePath).href);
    const changelog = readFileSync(resolve(repoRoot, 'CHANGELOG.md'), 'utf8');
    const section = /^## \[Unreleased\]\n(?<body>[\s\S]*?)(?=^## \[)/m.exec(changelog);
    assert.equal(section?.groups.body, '\nNo changes yet.\n\n');
    const changelogPath = join(root, 'CHANGELOG.md');
    writeFileSync(changelogPath, changelog);

    const version = '999.0.0';
    const notes = `# ${version}\n\n### Bug Fixes\n\n- generated regression note`;
    updateChangelog(version, notes);
    const expected = changelog.replace(
      section[0],
      `## [Unreleased]\n\nNo changes yet.\n\n## [${version}] - ${new Date().toISOString().slice(0, 10)}\n\n` +
        '### Bug Fixes\n\n- generated regression note\n\n',
    );
    assert.equal(readFileSync(changelogPath, 'utf8'), expected);

    for (const body of ['### Bug Fixes\n\n- handwritten note', 'No changes yet.\n\n- handwritten note']) {
      const manual = changelog.replace(section[0], `## [Unreleased]\n\n${body}\n\n`);
      writeFileSync(changelogPath, manual);
      assert.throws(
        () => updateChangelog(version, notes),
        /Unreleased contains manual content; refusing to overwrite it/,
      );
      assert.equal(readFileSync(changelogPath, 'utf8'), manual);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
