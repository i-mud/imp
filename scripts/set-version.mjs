#!/usr/bin/env node

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const repoRoot = resolve(import.meta.dirname, '..');

function output(message) {
  process.stdout.write(`${message}\n`);
}
const version = process.argv[2];

const semver =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

if (!version || !semver.test(version)) {
  console.error('usage: node scripts/set-version.mjs X.Y.Z');
  process.exit(2);
}

function path(relativePath) {
  return resolve(repoRoot, relativePath);
}

function read(relativePath) {
  return readFileSync(path(relativePath), 'utf8');
}

function write(relativePath, text) {
  writeFileSync(path(relativePath), text);
}

function readJson(relativePath) {
  return JSON.parse(read(relativePath));
}

function writeJson(relativePath, value) {
  write(relativePath, `${JSON.stringify(value, null, 2)}\n`);
}

function setJsonVersion(relativePath) {
  const value = readJson(relativePath);
  value.version = version;
  writeJson(relativePath, value);
}

function setTomlSectionVersion(relativePath, section) {
  const lines = read(relativePath).split(/\r?\n/);
  const header = `[${section}]`;
  const start = lines.findIndex((line) => line.trim() === header);

  if (start === -1) {
    throw new Error(`${relativePath}: missing ${header}`);
  }

  let found = -1;

  for (let i = start + 1; i < lines.length; i += 1) {
    if (lines[i].trim().startsWith('[')) break;

    if (/^\s*version\s*=\s*"[^"]+"\s*$/.test(lines[i])) {
      if (found !== -1) {
        throw new Error(`${relativePath}: multiple version entries in ${header}`);
      }

      found = i;
    }
  }

  if (found === -1) {
    throw new Error(`${relativePath}: ${header} has no version`);
  }

  lines[found] = lines[found].replace(/^(\s*version\s*=\s*")[^"]+("\s*)$/, `$1${version}$2`);

  write(relativePath, lines.join('\n'));
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function setLockPackageVersion(relativePath, packageName) {
  const blocks = read(relativePath).split(/(?=^\[\[package\]\]\s*$)/m);
  const namePattern = new RegExp(`^name\\s*=\\s*"${escapeRegExp(packageName)}"\\s*$`, 'm');

  let matches = 0;

  const updated = blocks.map((block) => {
    if (!namePattern.test(block)) return block;

    const versionPattern = /^version\s*=\s*"[^"]+"\s*$/m;

    if (!versionPattern.test(block)) {
      throw new Error(`${relativePath}: package ${JSON.stringify(packageName)} has no version`);
    }

    matches += 1;
    return block.replace(versionPattern, `version = "${version}"`);
  });

  if (matches !== 1) {
    throw new Error(
      `${relativePath}: expected exactly one ${JSON.stringify(packageName)} package, found ${matches}`,
    );
  }

  write(relativePath, updated.join(''));
}

setJsonVersion('package.json');
setJsonVersion('apps/desktop/package.json');
setJsonVersion('packages/protocol/package.json');
setJsonVersion('apps/desktop/src-tauri/tauri.conf.json');

const npmLock = readJson('package-lock.json');
npmLock.version = version;

for (const key of ['', 'apps/desktop', 'packages/protocol']) {
  const entry = npmLock.packages?.[key];

  if (!entry) {
    throw new Error(`package-lock.json: missing packages[${JSON.stringify(key)}]`);
  }

  entry.version = version;
}

writeJson('package-lock.json', npmLock);

setTomlSectionVersion('apps/desktop/src-tauri/Cargo.toml', 'package');
setLockPackageVersion('apps/desktop/src-tauri/Cargo.lock', 'imp-desktop');

setTomlSectionVersion('services/relay/pyproject.toml', 'project');
setLockPackageVersion('services/relay/uv.lock', 'imp-relay');

setTomlSectionVersion('integrations/tinyfugue/pyproject.toml', 'project');
setLockPackageVersion('integrations/tinyfugue/uv.lock', 'imp-tinyfugue');
setLockPackageVersion('integrations/tinyfugue/uv.lock', 'imp-relay');

output(`set-version: updated Imp release version to ${version}`);
