#!/usr/bin/env node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const repoRoot = resolve(import.meta.dirname, '..');

function read(relativePath) {
  return readFileSync(resolve(repoRoot, relativePath), 'utf8');
}

function readJson(relativePath) {
  return JSON.parse(read(relativePath));
}

function tomlSectionVersion(relativePath, section) {
  const text = read(relativePath);
  const lines = text.split(/\r?\n/);

  const sectionHeader = `[${section}]`;
  const start = lines.findIndex((line) => line.trim() === sectionHeader);

  if (start === -1) {
    throw new Error(`${relativePath}: missing ${sectionHeader}`);
  }

  for (let i = start + 1; i < lines.length; i += 1) {
    const line = lines[i].trim();

    if (line.startsWith('[')) break;

    const match = /^version\s*=\s*"([^"]+)"\s*$/.exec(line);
    if (match) return match[1];
  }

  throw new Error(`${relativePath}: ${sectionHeader} has no version`);
}

function lockPackageVersion(relativePath, packageName) {
  const text = read(relativePath);

  const blocks = text.split(/(?=^\[\[package\]\]\s*$)/m);
  const matches = [];

  for (const block of blocks) {
    const name = /^name\s*=\s*"([^"]+)"\s*$/m.exec(block)?.[1];
    if (name !== packageName) continue;

    const version = /^version\s*=\s*"([^"]+)"\s*$/m.exec(block)?.[1];
    if (!version) {
      throw new Error(`${relativePath}: package ${JSON.stringify(packageName)} has no version`);
    }

    matches.push(version);
  }

  if (matches.length !== 1) {
    throw new Error(
      `${relativePath}: expected exactly one ${JSON.stringify(packageName)} package, found ${matches.length}`,
    );
  }

  return matches[0];
}

const rootPackage = readJson('package.json');
const version = rootPackage.version;

const semver =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

if (typeof version !== 'string' || !semver.test(version)) {
  throw new Error(`package.json: invalid release version ${JSON.stringify(version)}`);
}

const npmLock = readJson('package-lock.json');
const desktopPackage = readJson('apps/desktop/package.json');
const protocolPackage = readJson('packages/protocol/package.json');
const tauriConfig = readJson('apps/desktop/src-tauri/tauri.conf.json');

const checks = [
  ['apps/desktop/package.json', desktopPackage.version],
  ['packages/protocol/package.json', protocolPackage.version],
  ['package-lock.json top-level', npmLock.version],
  ['package-lock.json root workspace', npmLock.packages?.['']?.version],
  ['package-lock.json desktop workspace', npmLock.packages?.['apps/desktop']?.version],
  ['package-lock.json protocol workspace', npmLock.packages?.['packages/protocol']?.version],
  ['apps/desktop/src-tauri/tauri.conf.json', tauriConfig.version],
  ['apps/desktop/src-tauri/Cargo.toml', tomlSectionVersion('apps/desktop/src-tauri/Cargo.toml', 'package')],
  [
    'apps/desktop/src-tauri/Cargo.lock imp-desktop',
    lockPackageVersion('apps/desktop/src-tauri/Cargo.lock', 'imp-desktop'),
  ],
  ['services/relay/pyproject.toml', tomlSectionVersion('services/relay/pyproject.toml', 'project')],
  ['services/relay/uv.lock imp-relay', lockPackageVersion('services/relay/uv.lock', 'imp-relay')],
  ['integrations/common/pyproject.toml', tomlSectionVersion('integrations/common/pyproject.toml', 'project')],
  [
    'integrations/common/uv.lock imp-adapter',
    lockPackageVersion('integrations/common/uv.lock', 'imp-adapter'),
  ],
  ['integrations/common/uv.lock imp-relay', lockPackageVersion('integrations/common/uv.lock', 'imp-relay')],
  ['integrations/mudlet/pyproject.toml', tomlSectionVersion('integrations/mudlet/pyproject.toml', 'project')],
  ['integrations/mudlet/uv.lock imp-mudlet', lockPackageVersion('integrations/mudlet/uv.lock', 'imp-mudlet')],
  [
    'integrations/mudlet/uv.lock imp-adapter',
    lockPackageVersion('integrations/mudlet/uv.lock', 'imp-adapter'),
  ],
  ['integrations/mudlet/uv.lock imp-relay', lockPackageVersion('integrations/mudlet/uv.lock', 'imp-relay')],
  [
    'integrations/tinyfugue/pyproject.toml',
    tomlSectionVersion('integrations/tinyfugue/pyproject.toml', 'project'),
  ],
  [
    'integrations/tinyfugue/uv.lock imp-tinyfugue',
    lockPackageVersion('integrations/tinyfugue/uv.lock', 'imp-tinyfugue'),
  ],
  [
    'integrations/tinyfugue/uv.lock imp-adapter',
    lockPackageVersion('integrations/tinyfugue/uv.lock', 'imp-adapter'),
  ],
  [
    'integrations/tinyfugue/uv.lock imp-relay',
    lockPackageVersion('integrations/tinyfugue/uv.lock', 'imp-relay'),
  ],
];

const failures = checks.filter(([, actual]) => actual !== version);

if (failures.length > 0) {
  console.error(`check-version: expected every release version to be ${version}`);

  for (const [location, actual] of failures) {
    console.error(`  ${location}: ${JSON.stringify(actual)}`);
  }

  process.exit(1);
}

const changelogHeading = `## [${version}]`;
if (!read('CHANGELOG.md').includes(changelogHeading)) {
  console.error(`check-version: CHANGELOG.md is missing release heading ${JSON.stringify(changelogHeading)}`);
  process.exit(1);
}

const args = process.argv.slice(2);

if (args.length > 0) {
  if (args.length !== 2 || args[0] !== '--tag') {
    console.error('usage: node scripts/check-version.mjs [--tag vX.Y.Z]');
    process.exit(2);
  }

  const expectedTag = `v${version}`;
  const actualTag = args[1];

  if (actualTag !== expectedTag) {
    console.error(
      `check-version: release tag ${JSON.stringify(actualTag)} does not match application version ${JSON.stringify(expectedTag)}`,
    );
    process.exit(1);
  }
}

console.warn(`check-version: ${version} is consistent across ${checks.length + 1} release version locations`);
