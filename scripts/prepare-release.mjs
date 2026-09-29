#!/usr/bin/env node

import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import semanticRelease from 'semantic-release';

import releaseConfig from '../release.config.mjs';
import { normalizeGeneratedReleaseNotes } from './release-notes.mjs';

const repoRoot = resolve(import.meta.dirname, '..');

function output(message) {
  process.stdout.write(`${message}\n`);
}

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

function refExists(ref) {
  return (
    spawnSync('git', ['show-ref', '--verify', '--quiet', ref], {
      cwd: repoRoot,
      stdio: 'ignore',
    }).status === 0
  );
}

function updateChangelog(version, notes) {
  const changelogPath = resolve(repoRoot, 'CHANGELOG.md');
  const changelog = readFileSync(changelogPath, 'utf8');

  const unreleased = /^## \[Unreleased\][^\r\n]*\r?\n(?<body>[\s\S]*?)(?=^## \[)/m;

  const match = unreleased.exec(changelog);

  if (!match) {
    throw new Error('CHANGELOG.md: could not locate the Unreleased section');
  }

  if (match.groups?.body.trim() !== 'No changes yet.') {
    throw new Error('CHANGELOG.md: Unreleased contains manual content; refusing to overwrite it');
  }

  const date = new Date().toISOString().slice(0, 10);
  const body = normalizeGeneratedReleaseNotes(notes, version);

  if (!body) {
    throw new Error('semantic-release generated empty release notes');
  }

  const replacement =
    `## [Unreleased]\n\n` + `No changes yet.\n\n` + `## [${version}] - ${date}\n\n` + `${body}\n\n`;

  writeFileSync(changelogPath, changelog.replace(unreleased, replacement));
}

const branch = capture('git', ['branch', '--show-current']);

if (branch !== 'main') {
  throw new Error(`release preparation must start from main; current branch is ${JSON.stringify(branch)}`);
}

if (capture('git', ['status', '--porcelain=v1'])) {
  throw new Error('release preparation requires a clean working tree');
}

run('git', ['fetch', '--tags', 'origin', 'main']);

const head = capture('git', ['rev-parse', 'HEAD']);
const remoteMain = capture('git', ['rev-parse', 'origin/main']);

if (head !== remoteMain) {
  throw new Error('local main is not exactly synchronized with origin/main');
}

const result = await semanticRelease(
  {
    ...releaseConfig,
    dryRun: true,
    ci: false,
  },
  {
    cwd: repoRoot,
    env: process.env,
  },
);

if (!result) {
  output('release: no release-relevant commits since the last version tag');
  process.exit(0);
}

const { version, gitTag, notes } = result.nextRelease;

if (gitTag !== `v${version}`) {
  throw new Error(`semantic-release produced unexpected tag ${JSON.stringify(gitTag)}`);
}

if (refExists(`refs/tags/${gitTag}`)) {
  throw new Error(`tag ${gitTag} already exists`);
}

const releaseBranch = `release/${gitTag}`;

if (refExists(`refs/heads/${releaseBranch}`)) {
  throw new Error(`local branch ${releaseBranch} already exists`);
}

if (capture('git', ['ls-remote', '--heads', 'origin', releaseBranch])) {
  throw new Error(`remote branch ${releaseBranch} already exists`);
}

output(`\nPreparing ${result.nextRelease.type} release ${gitTag} on ${releaseBranch}\n`);

run('git', ['switch', '-c', releaseBranch]);
run(process.execPath, ['scripts/set-version.mjs', version]);

updateChangelog(version, notes);

run('npx', [
  'prettier',
  '--write',
  'package.json',
  'package-lock.json',
  'apps/desktop/package.json',
  'packages/protocol/package.json',
  'apps/desktop/src-tauri/tauri.conf.json',
  'CHANGELOG.md',
]);

run('npm', ['run', 'release:check-version']);
run('git', ['diff', '--check']);

output(`
Release ${gitTag} is prepared but has NOT been committed or pushed.

Review the diff, run the full validation gates, then commit the release branch.
After its PR is merged, switch to updated main and run:

  npm run release:tag
`);
