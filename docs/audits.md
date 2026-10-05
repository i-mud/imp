# Repository Audits

Repository-wide technical audits are preserved as point-in-time engineering records.

Audits describe the repository as it existed when the review was performed. Their findings may no longer represent the current state after subsequent remediation.

## Conventions

- Store audit reports under `docs/audits/`.
- Use a dated, descriptive filename such as `2026-10-03-full-repository-audit.md`.
- Preserve completed audits as historical snapshots rather than updating findings as they are remediated.
- Distinguish reproduced defects from inferred risks, documented design limitations, and behavior that was not verified.
- Include relevant validation results and important limitations of the review.
- Prefer source-backed findings with concrete locations and evidence.
- Audit reports may propose remediation work, but the report itself is not the authoritative record of current defect status.
- Current behavior belongs in the relevant architecture, status, installation, development, or release documentation.

## Maintenance

When adding an audit:

1. Add the completed report under `docs/audits/`.
2. Add it to the index below.
3. Update any other documentation that should reference the audit.
4. Run the repository's documentation/path validation.

When removing, renaming, or relocating an audit:

1. Update this index.
2. Update references elsewhere in the documentation.
3. Run the repository's documentation/path validation.

Do not retroactively modify an audit merely because a finding has subsequently been fixed. Record remediation through normal implementation, tests, status/release documentation, and commits.

## Audits

- [`2026-10-03 full repository audit`](audits/2026-10-03-full-repository-audit.md) — Repository-wide review of architecture, state handling, trusted actions, transports, lifecycle, packaging, tests, and documentation. Identified 11 source-backed findings grouped into six remediation slices.
- [`2026-10-05 remediation closure`](audits/2026-10-05-remediation-closure.md) — Separate closure of the original audit's eleven findings through Slices 17–22 and the independently tracked Slice 23 ActionBroker timeout gap. Records landed remediation identities, current verification, and retained limitations; does not alter the original audit.
