import { defineConfig } from 'vitest/config';

// Not part of `npm test`: scripts/test-direct-code-e2e.py runs this with the issued runtime configs on stdin.
export default defineConfig({
  test: {
    name: 'direct-code-e2e',
    environment: 'node',
    include: ['test/direct-code.e2e.ts'],
    globalSetup: ['test/direct-code-global-setup.ts'],
    reporters: ['./test/direct-code-reporter.ts'],
    // Hang guards only; every step waits on an event, never on elapsed time.
    testTimeout: 60_000,
    hookTimeout: 60_000,
  },
});
