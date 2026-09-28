#!/usr/bin/env node
/**
 * Fail the build when documentation references a path that does not exist.
 *
 * The architecture map's value depends entirely on its citations being real.
 * A card pointing at a moved or deleted file is worse than no card, because a
 * reader trusts it. This makes every cited path executable evidence rather
 * than an assertion, which is the only drift check that can be automated
 * honestly: it detects broken references, and never invents architecture.
 */
import { readFileSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { globSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const repoRoot = resolve(import.meta.dirname, '..');

/**
 * Paths the documentation deliberately names before they exist, because the
 * reader is being told to create them. Keep this list at zero where possible.
 */
const PLANNED = new Set();

const SKIP_PREFIX = ['http', 'ws:', 'wss:', 'mailto', '@', 'imp_', 'dev.', '\\'];

const docs = globSync('**/*.md', {
  cwd: repoRoot,
  exclude: (name) => ['node_modules', '.venv', 'dist', 'target', '__pycache__', '.git'].includes(name),
});

const backticked = /`([A-Za-z0-9_./@-]+\/[A-Za-z0-9_./@-]+)`/g;
const linked = /\]\(([^)]+)\)/g;

const problems = [];
let checked = 0;

for (const relativeDoc of docs.sort()) {
  const text = readFileSync(join(repoRoot, relativeDoc), 'utf8');
  const docDir = dirname(join(repoRoot, relativeDoc));

  const candidates = [];
  for (const match of text.matchAll(backticked)) candidates.push(match[1].replace(/[.,;:]+$/, ''));
  for (const match of text.matchAll(linked)) candidates.push(match[1].split('#')[0]);

  for (const candidate of candidates) {
    if (candidate === '' || candidate.includes('*') || candidate.includes(' ')) continue;
    if (SKIP_PREFIX.some((prefix) => candidate.startsWith(prefix))) continue;
    if (PLANNED.has(candidate)) continue;

    checked += 1;
    if (!existsSync(join(repoRoot, candidate)) && !existsSync(join(docDir, candidate))) {
      problems.push(`${relativeDoc}: ${candidate}`);
    }
  }
}

if (problems.length > 0) {
  console.error(`check-docs: ${problems.length} broken reference(s) in ${docs.length} files\n`);
  for (const problem of problems) console.error(`  ${problem}`);
  console.error('\nEither fix the path or, if it is a file the reader must create, add it to PLANNED.');
  process.exit(1);
}

console.warn(`check-docs: ${checked} path references across ${docs.length} markdown files all resolve`);
