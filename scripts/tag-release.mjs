#!/usr/bin/env node

import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import semanticRelease from 'semantic-release';

import releaseConfig from '../release.config.mjs';
import {
  canonicalizeReleaseNotes,
  extractChangelogReleaseNotes,
  normalizeGeneratedReleaseNotes,
} from './release-notes.mjs';

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

const branch = capture('git', ['branch', '--show-current']);

if (branch !== 'main') {
  throw new Error(`release tagging must run from main; current branch is ${JSON.stringify(branch)}`);
}

if (capture('git', ['status', '--porcelain=v1'])) {
  throw new Error('release tagging requires a clean working tree');
}

run('git', ['fetch', '--tags', 'origin', 'main']);

const head = capture('git', ['rev-parse', 'HEAD']);
const remoteMain = capture('git', ['rev-parse', 'origin/main']);

if (head !== remoteMain) {
  throw new Error('local main is not exactly synchronized with origin/main');
}

run('npm', ['run', 'release:check-version']);

const packageJson = JSON.parse(readFileSync(resolve(repoRoot, 'package.json'), 'utf8'));

const version = packageJson.version;
const tag = `v${version}`;

if (refExists(`refs/tags/${tag}`)) {
  throw new Error(`tag ${tag} already exists locally`);
}

if (capture('git', ['ls-remote', '--tags', 'origin', `refs/tags/${tag}`])) {
  throw new Error(`tag ${tag} already exists on origin`);
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
  throw new Error(`semantic-release sees no pending release, but package.json is ${version}`);
}

if (result.nextRelease.version !== version) {
  throw new Error(
    `semantic-release expects ${result.nextRelease.version}, but the prepared tree is ${version}`,
  );
}

if (result.nextRelease.gitTag !== tag) {
  throw new Error(`semantic-release expects tag ${result.nextRelease.gitTag}, not ${tag}`);
}

const generatedNotes = normalizeGeneratedReleaseNotes(result.nextRelease.notes, version);
const changelog = readFileSync(resolve(repoRoot, 'CHANGELOG.md'), 'utf8');
const changelogNotes = extractChangelogReleaseNotes(changelog, version);

if (canonicalizeReleaseNotes(generatedNotes) !== canonicalizeReleaseNotes(changelogNotes)) {
  throw new Error(
    `CHANGELOG.md release notes for ${version} no longer match semantic-release; prepare the release again`,
  );
}

run('git', ['tag', '-s', '-m', `Imp ${tag}`, tag, 'HEAD']);

const tagObject = capture('git', ['cat-file', '-p', tag]);

if (!tagObject.includes('BEGIN SSH SIGNATURE')) {
  run('git', ['tag', '-d', tag]);
  throw new Error(`tag ${tag} was not SSH-signed; refusing to publish it`);
}

output(`\nCreated signed annotated tag ${tag}. Pushing it now.\n`);

run('git', ['push', 'origin', `refs/tags/${tag}`]);

output(`
Published ${tag}.

The tag-triggered Release workflow now owns artifact build, verification,
and GitHub Release publication.
`);
