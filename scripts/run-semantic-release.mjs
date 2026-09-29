#!/usr/bin/env node

import { appendFileSync } from 'node:fs';

import semanticRelease from 'semantic-release';

if (process.env.GITHUB_ACTIONS !== 'true') {
  throw new Error('release:ci may only run in GitHub Actions; use npm run release:next for a local preview');
}

const outputPath = process.env.GITHUB_OUTPUT;

if (!outputPath) {
  throw new Error('GITHUB_OUTPUT is not available');
}

const result = await semanticRelease();

if (!result?.nextRelease) {
  appendFileSync(outputPath, 'released=false\n');
  process.stdout.write('semantic-release: no release required\n');
  process.exit(0);
}

const { version, gitTag } = result.nextRelease;

appendFileSync(outputPath, 'released=true\n');
appendFileSync(outputPath, `version=${version}\n`);
appendFileSync(outputPath, `tag=${gitTag}\n`);

process.stdout.write(`semantic-release: published ${gitTag}\n`);
