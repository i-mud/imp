import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { parseConnectionCode } from '../src/lib/connection-code.ts';

const vectors: {
  valid: { name: string; code: string; endpoint: string; token: string }[];
  invalid: { name: string; code: string }[];
} = JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/direct-connection-codes.json', import.meta.url), 'utf8'),
);

describe('parseConnectionCode shared vectors', () => {
  it.each(vectors.valid)('accepts $name and returns only the endpoint', ({ code, endpoint }) => {
    expect(parseConnectionCode(code)).toBe(endpoint);
  });

  it.each(vectors.invalid)('rejects $name', ({ code }) => {
    expect(parseConnectionCode(code)).toBeNull();
  });

  it('bounds the raw input before trimming', () => {
    const { code } = vectors.valid[0]!;
    expect(parseConnectionCode(code + ' '.repeat(513 - code.length))).toBeNull();
    expect(parseConnectionCode(`\u00e9${code}`)).toBeNull();
  });
});
