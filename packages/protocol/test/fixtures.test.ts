import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { decodeClientMessage, decodeServerMessage } from '../src/decode.ts';
import type { DecodeResult } from '../src/result.ts';

/**
 * The fixture corpus is the cross-language conformance suite: this file and
 * `services/relay/tests/test_protocol_fixtures.py` consume the same JSON, so
 * the TypeScript and Python decoders cannot drift apart silently.
 */
const FIXTURE_ROOT = join(import.meta.dirname, '..', 'fixtures');

interface Fixture {
  readonly name: string;
  readonly description: string;
  readonly direction: 'server' | 'client';
  readonly frame: string;
  readonly code?: string;
  readonly path?: string;
}

function load(bucket: 'accept' | 'reject'): Fixture[] {
  const dir = join(FIXTURE_ROOT, bucket);
  return readdirSync(dir)
    .filter((entry) => entry.endsWith('.json'))
    .sort()
    .map((entry) => ({
      name: entry.replace(/\.json$/, ''),
      ...(JSON.parse(readFileSync(join(dir, entry), 'utf8')) as Omit<Fixture, 'name'>),
    }));
}

function decode(fixture: Fixture): DecodeResult<unknown> {
  return fixture.direction === 'client'
    ? decodeClientMessage(fixture.frame)
    : decodeServerMessage(fixture.frame);
}

const accepted = load('accept');
const rejected = load('reject');

describe('fixture corpus', () => {
  // Guards against a path typo turning the whole suite into a no-op.
  it('is populated', () => {
    expect(accepted.length).toBeGreaterThan(5);
    expect(rejected.length).toBeGreaterThan(15);
  });
});

describe.each(accepted)('accept/$name', (fixture) => {
  it(fixture.description, () => {
    const result = decode(fixture);
    expect(result.ok ? null : result.error).toBeNull();
  });
});

describe.each(rejected)('reject/$name', (fixture) => {
  it(fixture.description, () => {
    const result = decode(fixture);
    if (result.ok) throw new Error('expected rejection but the frame was accepted');
    expect({ code: result.error.code, path: result.error.path }).toEqual({
      code: fixture.code,
      path: fixture.path,
    });
  });
});
