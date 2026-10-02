import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  canonicalizeReleaseNotes,
  extractChangelogReleaseNotes,
  normalizeGeneratedReleaseNotes,
} from './release-notes.mjs';

const repoRoot = resolve(import.meta.dirname, '..');

const releaseFiles = [
  'CHANGELOG.md',
  'package.json',
  'package-lock.json',
  'apps/desktop/package.json',
  'packages/protocol/package.json',
  'apps/desktop/src-tauri/tauri.conf.json',
  'apps/desktop/src-tauri/Cargo.toml',
  'apps/desktop/src-tauri/Cargo.lock',
  'services/relay/pyproject.toml',
  'services/relay/uv.lock',
  'integrations/common/pyproject.toml',
  'integrations/common/uv.lock',
  'integrations/mudlet/pyproject.toml',
  'integrations/mudlet/uv.lock',
  'integrations/tinyfugue/pyproject.toml',
  'integrations/tinyfugue/uv.lock',
];

function capture(command, args) {
  return execFileSync(command, args, {
    cwd: repoRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'],
  }).trim();
}

function run(command, args) {
  execFileSync(command, args, {
    cwd: repoRoot,
    stdio: 'inherit',
  });
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function releaseHeadingExists(changelog, version) {
  return new RegExp(`^## \\[${escapeRegExp(version)}\\]`, 'm').test(changelog);
}

function updateChangelog(version, notes) {
  const changelogPath = resolve(repoRoot, 'CHANGELOG.md');
  const changelog = readFileSync(changelogPath, 'utf8');

  if (releaseHeadingExists(changelog, version)) {
    throw new Error(`CHANGELOG.md already contains release ${version}`);
  }

  const unreleased = /^## \[Unreleased\][^\r\n]*\r?\n(?<body>[\s\S]*?)(?=^## \[)/m;
  const match = unreleased.exec(changelog);

  if (!match) {
    throw new Error('CHANGELOG.md: could not locate the Unreleased section');
  }

  if (match.groups?.body.trim() !== 'No changes yet.') {
    throw new Error('CHANGELOG.md: Unreleased contains manual content; refusing to overwrite it');
  }

  const body = normalizeGeneratedReleaseNotes(notes, version);

  if (!body) {
    throw new Error('semantic-release generated empty release notes');
  }

  const date = new Date().toISOString().slice(0, 10);
  const replacement =
    `## [Unreleased]\n\n` + `No changes yet.\n\n` + `## [${version}] - ${date}\n\n` + `${body}\n\n`;

  writeFileSync(changelogPath, changelog.replace(unreleased, replacement));
}

function assertPreparedNotesMatch(version, notes) {
  const changelog = readFileSync(resolve(repoRoot, 'CHANGELOG.md'), 'utf8');
  const generated = normalizeGeneratedReleaseNotes(notes, version);
  const recorded = extractChangelogReleaseNotes(changelog, version);

  if (canonicalizeReleaseNotes(generated) !== canonicalizeReleaseNotes(recorded)) {
    throw new Error(`CHANGELOG.md release notes for ${version} no longer match semantic-release`);
  }
}

function assertSignedCommit() {
  const commitObject = capture('git', ['cat-file', '-p', 'HEAD']);

  if (!commitObject.includes('BEGIN SSH SIGNATURE')) {
    throw new Error('release metadata commit is not SSH-signed');
  }
}

export async function prepare(_pluginConfig, context) {
  const { branch, lastRelease, logger, nextRelease, options } = context;

  if (options.dryRun) {
    logger.log('Dry run: skip release metadata commit');
    return;
  }

  if (capture('git', ['status', '--porcelain=v1'])) {
    throw new Error('semantic-release prepare requires a clean working tree');
  }

  const { version, notes } = nextRelease;
  const packageJson = JSON.parse(readFileSync(resolve(repoRoot, 'package.json'), 'utf8'));
  const currentVersion = packageJson.version;
  const changelog = readFileSync(resolve(repoRoot, 'CHANGELOG.md'), 'utf8');
  const alreadyPrepared = currentVersion === version && releaseHeadingExists(changelog, version);

  if (alreadyPrepared) {
    assertPreparedNotesMatch(version, notes);
    run('npm', ['run', 'release:check-version']);

    const subject = capture('git', ['log', '-1', '--format=%s']);
    const expectedSubject = `chore(release): ${version} [skip ci]`;

    if (subject !== expectedSubject) {
      throw new Error(`release metadata for ${version} exists, but HEAD is not the expected release commit`);
    }

    assertSignedCommit();

    nextRelease.gitHead = capture('git', ['rev-parse', 'HEAD']);
    logger.log(`Reusing already-prepared release commit ${nextRelease.gitHead}`);
    return;
  }

  if (currentVersion !== lastRelease.version) {
    throw new Error(`package.json is ${currentVersion}, but the last release is ${lastRelease.version}`);
  }

  run(process.execPath, ['scripts/set-version.mjs', version]);
  updateChangelog(version, notes);

  run('npx', [
    'prettier',
    '--write',
    'CHANGELOG.md',
    'package.json',
    'package-lock.json',
    'apps/desktop/package.json',
    'packages/protocol/package.json',
    'apps/desktop/src-tauri/tauri.conf.json',
  ]);

  run('npm', ['run', 'release:check-version']);
  run('git', ['diff', '--check']);
  run('git', ['add', '--', ...releaseFiles]);

  const staged = spawnSync('git', ['diff', '--cached', '--quiet'], {
    cwd: repoRoot,
    stdio: 'inherit',
  });

  if (staged.status === 0) {
    throw new Error(`release ${version} produced no metadata changes`);
  }

  if (staged.status !== 1) {
    throw new Error('could not inspect staged release metadata');
  }

  const message = `chore(release): ${version} [skip ci]\n\n` + normalizeGeneratedReleaseNotes(notes, version);

  run('git', ['commit', '-m', message]);
  assertSignedCommit();

  const head = capture('git', ['rev-parse', 'HEAD']);

  run('git', ['push', 'origin', `HEAD:${branch.name}`]);

  const remoteHead = capture('git', ['ls-remote', '--heads', 'origin', branch.name]).split(/\s+/)[0];

  if (remoteHead !== head) {
    throw new Error(`origin/${branch.name} did not advance to the release commit`);
  }

  // semantic-release captures gitHead before prepare hooks run. This repository
  // deliberately creates a signed release-metadata commit during prepare, so
  // point the final release tag at that new commit.
  nextRelease.gitHead = head;

  logger.log(`Prepared and pushed signed release commit ${head}`);
}
