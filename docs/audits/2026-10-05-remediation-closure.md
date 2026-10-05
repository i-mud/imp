# Repository audit remediation closure — 2026-10-05

## Purpose and scope

Close the remediation program spanning Slices 17–23, not a new product slice.
This is a documentation-only reconciliation: no runtime or protocol changes,
commit, tag creation, release publication, deployment, or live MUD actions.

The [2026-10-03 repository audit](2026-10-03-full-repository-audit.md) is historical
and immutable. This closure record does not alter its findings. Closure means
that the identified finding has been remediated in current `main`; it does not
mean every platform/runtime scenario has been exhaustively revalidated.

## Audit baseline

The original report identifies its baseline as **v0.2.1 plus post-release
documentation/CI preparation work**, with 11 findings: five Medium and six Low.
It was preserved by commit `da0cd80` and merged through PR #29 (`564f8af`).
Its Git blob is `a5df97852e6c6359716b636111626a27da66f1cd`, unchanged from
that preservation commit, pre-pass state, and the origin main ref.

This pass started with clean `main`, fetched origin, and fast-forwarded through
release metadata to HEAD **`2ee45a10dabcdb86f2583c7a2f1d05bad5d44de6`**
(`v0.2.8`). HEAD and the origin main ref agree. No closure branch was required.

## Remediation summary

Git ancestry, merged GitHub PR metadata, current source/test spot-checks, and
slice-specific evidence in [status](../status.md) independently establish the
mapping below. Roadmap prose alone is not closure evidence. All numbered
findings are remediated; the separately tracked broker timeout gap is resolved.

### Finding-by-finding closure matrix

Summaries retain the historical audit's finding titles. Verification combines
current source/test presence with the final gate below; previously recorded
native/browser/packaged acceptance is explicitly historical evidence.

| Finding | Severity | Summary                                                                  | Remediation / primary components                                                              | PR                                                                                                      | Verification                                                                                                                                                                            | Status     |
| ------- | -------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| F01     | Medium   | Bounded hostile GMCP can crash adapters or leave partially applied state | Slice 17: shared `records.py` / `normalize.py`, TinyFugue and Mudlet rejection paths          | [#30](https://github.com/i-mud/imp/pull/30)                                                             | Bounded nesting/conversion and staged atomic normalization remain present; adapter regressions and loopback evidence in status                                                          | Remediated |
| F02     | Medium   | Repeated unchanged GMCP produces false stale-feed status                 | Slice 18: shared observation classification, adapter publication, relay freshness             | [#31](https://github.com/i-mud/imp/pull/31)                                                             | Unchanged valid selected state observations refresh liveness without advancing sequence; ignored/rejected/text input does not; adapter/relay regressions                                | Remediated |
| F03     | Medium   | A backpressured subscriber stalls ingest and healthy subscribers         | Slice 19: relay `server.py`, bounded subscriber delivery                                      | [#32](https://github.com/i-mud/imp/pull/32), test follow-up [#33](https://github.com/i-mud/imp/pull/33) | Nonblocking enqueue, 16-frame FIFO and 5-second send deadline; relay/gateway backpressure tests and recorded real-socket evidence                                                       | Remediated |
| F04     | Medium   | Gateway DEBUG logging exposes the full pairing token                     | Slice 20: `websocket_logging.py`, gateway/server owned connections                            | [#34](https://github.com/i-mud/imp/pull/34)                                                             | Owned logger retains an INFO floor under application DEBUG; credential-log regressions and recorded real authentication exchanges                                                       | Remediated |
| F05     | Medium   | Desktop-owned children lack hard-crash containment                       | Slice 21: native `runtime.rs`, Windows/Unix implementations and node/gateway/tunnel ownership | [#35](https://github.com/i-mud/imp/pull/35)                                                             | Atomic Windows Job membership/kill-on-close; Unix guardian/lifetime pipe; existing ownership/parity tests and recorded Windows hard-death/relaunch acceptance, within platform contract | Remediated |
| F06     | Low      | Oversized JSON integers escape the Python protocol decoder               | Slice 17: relay `protocol.py`                                                                 | [#30](https://github.com/i-mud/imp/pull/30)                                                             | Integer range checks avoid float overflow; oversized-number fixtures and policy-close regressions                                                                                       | Remediated |
| F07     | Low      | Infinite configuration durations defeat finite bounds                    | Slice 17: relay/gateway config and state/auth constructor boundaries                          | [#30](https://github.com/i-mud/imp/pull/30)                                                             | Finite-positive stale/auth duration validation; constructor/config regressions and recorded CLI rejection                                                                               | Remediated |
| F08     | Low      | Shared HUD feedback incorrectly identifies TinyFugue                     | Slice 22: action `presentation.ts`, HUD/connection guidance                                   | [#36](https://github.com/i-mud/imp/pull/36)                                                             | Shared forwarding says game client; current neutral consumer rejection contract; presentation tests and recorded Chromium surfaces                                                      | Remediated |
| F09     | Low      | Custom radio groups omit expected keyboard behavior                      | Slice 22: Settings, Connection and Alert dialogs                                              | [#36](https://github.com/i-mud/imp/pull/36)                                                             | Labelled native radios in named groups, visible focus; rendered-semantics tests and recorded Chromium Tab/arrow acceptance                                                              | Remediated |
| F10     | Low      | The TinyFugue acceptance procedure calls a removed factory               | Slice 22: TinyFugue README                                                                    | [#36](https://github.com/i-mud/imp/pull/36)                                                             | Guide uses async `createRuntimeClients()` and returned `actionSink`; recorded browser execution of the current factory                                                                  | Remediated |
| F11     | Low      | Relay hello advertises the obsolete implementation version               | Slice 20: relay server and frozen-node metadata packaging                                     | [#34](https://github.com/i-mud/imp/pull/34)                                                             | Hello uses `importlib.metadata.version("imp-relay")`; frozen build copies package metadata; version/hello regressions and recorded installed/frozen checks                              | Remediated |

### Landed remediation references

All PRs below are merged into `main`; each merge and feature commit is in current
HEAD's ancestry. Short hashes identify verified commits, not inferred markers.

| Slice             | Slug                              | PR  | Merge commit                               | Feature commit |
| ----------------- | --------------------------------- | --- | ------------------------------------------ | -------------- |
| 17                | `input-boundary-correctness`      | #30 | `832d974c4436db0341af11ec194df33913bfbdfd` | `41687db`      |
| 18                | `freshness-correctness`           | #31 | `91f06f949ae4135a9a8ece6dcf7d950d60740257` | `6384e56`      |
| 19                | `subscriber-isolation`            | #32 | `b3769e6d5bc029217ae7d78428b0b56ab2d3b6ea` | `edb486c`      |
| 19 test follow-up | Gateway backpressure regression   | #33 | `525c167ad18eb21a44e510bfe31722ea6106b962` | `fbf5cf6`      |
| 20                | `diagnostic-hardening`            | #34 | `1102cc24802ddbd7f6259b105800293f3f6d124a` | `fcb1bdc`      |
| 21                | `native-runtime-ownership`        | #35 | `21667559b9aa42524513ff8a9378cda8d1d35a27` | `dd49555`      |
| 22                | `client-neutral-ui-docs`          | #36 | `fe2515cb6e7588dfaf7c0e2b9b8029c455c62f2f` | `b513e7e`      |
| 23                | `actionbroker-timeout-validation` | #37 | `3cfbdc70c45af7c4697d9b34006923254555313f` | `a98d1ca`      |

PR #33 corrects flaky gateway backpressure test assumptions, not production
subscriber behavior. The Windows executable-resolution/parity correction is
included in PR #35's final feature commit and merged tests, not a separate
follow-up PR. Intermediate review commits are not required to identify the
landed remediation.

## Deferred hardening item resolved

Slice 23 / [PR #37](https://github.com/i-mud/imp/pull/37) resolves a **separately
tracked hardening gap**, not an original numbered finding. Direct `ActionBroker`
construction previously accepted invalid result timeouts. Finite-positive
validation now runs at the constructor boundary, rejecting zero, negative,
infinite, and NaN values immediately with `ValueError`. Valid positive values
and the production five-second default remain accepted.

Direct-construction regressions and a real loopback timeout/recovery test cover
`unknown` on missing consumer result, late-result rejection, release of in-flight
ownership, and subsequent successful dispatch. No invented finding number,
new setting, upper bound, retry, or action-contract change is associated with
this item. It is resolved.

## Current documentation reconciliation

The documentation search covered numbered findings, outstanding/deferred/audit
terms, ActionBroker/result-timeout terms, and Slices 17–23 across current project
docs and integration guides. Historical findings and slice-specific scope
statements are evidence, not current outstanding-state claims.

- [Status](../status.md): add program closure reference; distinguish historical
  Slice 21 scope exclusions from current remediation; retain separate known
  deferred issues and all platform/live-evidence limits.
- [Roadmap](../roadmap.md): add missing Slice 23 progress row, mark Slice 21
  complete within its documented contract, and link program closure. No next
  slice is selected; existing candidates remain provisional.
- [Audit index](../audits.md): retain the original audit and add this separate
  closure record.
- [Release documentation](../releases.md): remove the stale latest-release
  `v0.2.1` assertion in favor of the authoritative GitHub Releases listing
  (observed latest publication: `v0.2.8`). Release rules are unchanged.

No current tracking claim leaves a numbered finding or the broker timeout gap
outstanding. Identity reacquisition, upstream TinyFugue crash investigation, and
raw/ANSI GMCP robustness remain separately documented candidate work, not
reopened audit findings. No Slice 24 scope is created.

## Milestone/tag assessment

Existing development markers use descriptive slugs, separately from automated
`vX.Y.Z` releases. Historical marker coverage is not perfectly uniform: later
post-Slice-9 and post-Slice-15 work was already untagged. Recent numbered Slices
17–23 lack milestone tags, although automated releases `v0.2.2`–`v0.2.8` cover
the remediation period. Existing relevant milestones include
`native-connection-settings`, `first-release-readiness`, `imp-rename`, and
`server-install-bootstrap`. The roadmap previously presented Slice 16's slug
`mudlet-local-integration` as a tag, but neither the local inventory nor an exact
origin tag query contains it; the roadmap now records Slice 16 as untagged.

Optional backfilling would improve navigation, not correctness or release
history. Proposed names reuse the documented slugs in the landed-reference
table above; each would point to its slice's merge commit. For
`subscriber-isolation`, prefer `525c167ad18eb21a44e510bfe31722ea6106b962` to
include the separately merged test-only stability correction. No tags were
created; these are proposals requiring separate authorization, not release tags.

## Validation state

Executed in this pass after documentation edits:

- `git diff --check`: passed.
- Documentation/reference checks: 399 path references across 47 Markdown files
  resolve.
- Version checks: `0.2.8` consistent across 23 release version locations.
- Full `npm run check`: passed (exit 0), including lint/formatting, TypeScript
  and Svelte checks (zero errors/warnings), protocol 64 tests, desktop 152,
  relay 226, shared adapter 31, Mudlet 32, TinyFugue 101, real loopback E2E,
  and frontend production build. No tests reported skipped.

The first gate attempt rejected two code-formatted origin-ref names as file
paths; wording was corrected and the complete gate passed on rerun.
Native acceptance, frozen-package builds, browser interaction, and live MUD/VPS
checks were not rerun. Historical evidence remains in [status](../status.md);
this pass checked landed implementation and regression coverage, not every
historical acceptance scenario.

Remediation-specific regression files remain present. No relevant Python
skip/xfail markers were found. Native ownership and Windows executable-resolution
suites require the `runtime-acceptance` feature and run separately from the npm
gate. The two ignored Windows parity dispatcher tests are invoked by subprocess
cases; they are not missing acceptance cases.

## Remaining known limitations / intentionally unverified areas

- Windows Job Object ownership covers desktop-owned runtime trees, not adopted
  endpoints, external processes, or independently broker-created processes.
- Unix containment supports foreground-owned process trees via guardian/private
  process groups. Deliberate group/session escape, stopped/failed guardians, and
  independently persistent/backgrounded nested SSH proxy/jump clients are not
  claimed to be contained. External masters/forwards remain unowned.
- Linux ownership runtime evidence is recorded. Darwin source/API typechecking
  is recorded, but native macOS runtime acceptance and full desktop build are
  not established; Linux/macOS desktop release acceptance is not claimed.
- Slice 22 Chromium acceptance is browser evidence, not new native Windows,
  live-MUD, or VPS acceptance. Node-only rendered radio tests do not themselves
  prove keyboard interaction.
- The platform-independent gate does not compile Rust or run feature-gated native
  ownership acceptance. Existing native evidence is not silently upgraded by a
  documentation gate.

## Closure conclusion

All six closure criteria are satisfied: every original finding maps to a landed
remediation still present on main; current documentation has no contradictory
outstanding-state claim; the historical audit is unchanged; the separate broker
timeout gap is resolved; and repository validation passes. **The remediation
program is closed**, within the documented contracts and limitations above.
No additional unresolved item in this remediation scope was discovered.
The documentation-only changes are ready for independent review, uncommitted.
