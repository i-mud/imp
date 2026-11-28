import type { Reporter, TestModule } from 'vitest/node';

// The only output of the Direct fixture run: fixed labels and counts. Test console output, stacks and
// assertion values (which could hold a pairing token) are never forwarded.
export default class DirectCodeReporter implements Reporter {
  onTestRunEnd(modules: ReadonlyArray<TestModule>, errors: ReadonlyArray<unknown>): void {
    let passed = 0;
    let total = 0;
    for (const module of modules) {
      for (const test of module.children.allTests()) {
        total += 1;
        const result = test.result();
        if (result.state === 'passed') passed += 1;
        const label = result.state === 'failed' ? result.errors?.[0]?.message : undefined;
        const fixed = label?.startsWith('DIRECT-E2E-FAIL ') ? label : 'DIRECT-E2E-FAIL unlabelled failure';
        process.stdout.write(`DIRECT-E2E-RESULT ${result.state} ${test.name}\n`);
        if (result.state === 'failed') process.stdout.write(`${fixed}\n`);
      }
    }
    process.stdout.write(`DIRECT-E2E-SUMMARY passed=${passed} total=${total} errors=${errors.length}\n`);
  }
}
