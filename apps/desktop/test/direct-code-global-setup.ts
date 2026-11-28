import type { TestProject } from 'vitest/node';

// Runs in the Vitest main process, which inherits the fixture's stdin pipe. The configs reach the test
// worker through `provide`, never through a file or the environment.
export default async function setup(project: TestProject): Promise<void> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  try {
    project.provide('directCodeRuntimeConfigs', JSON.parse(Buffer.concat(chunks).toString('utf8')));
  } catch {
    // A JSON syntax error message can quote the input.
    throw new Error('DIRECT-E2E-FAIL runtime configs were not piped to stdin as JSON');
  }
}
