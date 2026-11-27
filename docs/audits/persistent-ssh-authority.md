# Persistent SSH and native route authority (Slice 29.3b)

**Decision C — reject this candidate as the Guided gameplay foundation.** On both native clients it authenticated once and preserved both TCP half-closes. But a blocked download stopped a sibling echo, the paired action p95 missed the declared tolerance, and a replacement backend accepted an action against state still arriving from its predecessor. These are observed failures, not reasons to ship behind a readiness check. Neither library integration nor renderer/live switching is approved.

## Question and control

Can a single native-owned SSH session both carry gameplay traffic at warm-forward latency and prevent work on a retired route from reaching its replacement? These are separate gates. Successful forwarding is not permission to act.

[PR #69](https://github.com/i-mud/imp/pull/69) merged as `90dec6a4c77bc310a3a9f674e797025c52113833` on 2026-11-27. Its reviewed head was `12accb190bb8742451e45205ff03253f56c6a720`. It preserved a **negative** result, not an approved Guided runtime. In its single 20-action runs, warm OpenSSH versus one `-W` per connection had p50/p95 of 2.6/3.5 versus 154.4/161.1 ms on Linux, and 22.3/51.1 versus 161.1/174.6 ms on Windows. Real state, exact-context fixture delivery, cancellation and ownership isolation worked. Server-first half-close failed **in that candidate**. See the [29.3a measurements](guided-ssh-forward.md); this experiment does not rerun or rehabilitate `-W`.

Compare the candidate with a **new**, already authenticated system `ssh -N -L` under equivalent conditions on each machine. Measure from starting a short action until receiving its result, not until channel cleanup. Keep cold authentication, first snapshot and recovery separate. Pair 20 native and 20 warm actions in each of three runs. For each of p50 and p95, compare that quantile of the 60 paired latency differences with the largest of 5 ms, 25% of the median warm run quantile, and the warm run quantiles' median absolute deviation. The absolute floor accommodates local scheduling/driver noise at very low baseline latencies; the relative and measured-variation terms scale on slower hosts. This is an engineering screening threshold, not a gameplay SLO or confidence interval. A held state socket must also continue delivering fresh updates. Do not use the HUD timeout as a performance target.

## What the authority can identify

Today's `RelayStateSource` retries a URL, and `RelayActionSink.send` constructs a separate WebSocket from a URL. Neither receives the native supervisor's authenticated session handle. Port ownership up to teardown, a health response, or a readiness check before dialing cannot protect the next socket after teardown.

The experiment chooses **native-owned WebSockets** instead of introducing a renderer-facing token and a new local network service. The raw listener used to test TCP is not an authority API. State, retries and actions must use the same current native session; replacement must discard its predecessor's context and invocation state. A fixture process speaking to native code over inherited pipes is not the production WebView or a Tauri authorization boundary.

There are three distinct identities: the SSH server proved by the host key, the native connection to that server, and the producer/character described by the relay. Each `direct-tcpip` open asks the SSH server for a **new** TCP connection to its loopback listener. A generation identifies the SSH connection, not the process accepting that new connection. Context equality is neither a backend-incarnation identifier nor evidence of fresh gameplay. Do not infer end-to-end character identity from these tests or a matching relay name.

[Candidate and dependency comparison](persistent-ssh-candidates.md). The experiment is separate from the installed application: no Tauri command, renderer selector, config schema, server change, release or live switching.

The native request loop serializes admission on a single Tokio runtime thread. Its generation is scoped to that process and its inherited control pipe, not a bearer secret that can be exported to a new owner. Within a generation, a foreground revision distinguishes changing away from a character and returning to the same values. Dispatch needs the current ticket, a matching live feed, a still-open session and capacity. An attempted write makes a subsequent loss **unknown**, not permission to retry. State-channel loss retires the route; an explicit replacement starts without an actionable character. The existing renderer's `invokeAction` writes shared feedback and clears the pending ID in `finally` even after an asynchronous return. A future renderer adapter would have to discard stale state **and** fence those result/cleanup writes; this experiment installs no such adapter.

## Reproduction and cost

From the repository root on **each native client**, with Cargo, Docker running Linux containers, the local relay's Python prerequisites and system OpenSSH client tools:

```sh
cargo build --locked --manifest-path apps/desktop/src-tauri/tests/persistent-ssh/Cargo.toml
uv run --locked --project services/relay python -u scripts/run-persistent-ssh-fixture.py apps/desktop/src-tauri/tests/persistent-ssh/target/debug/persistent-ssh
```

On native Windows use the `.exe` suffix and run from the native mirror. Only the fixture SSH endpoints and the synthetic test controller are published, on host loopback. The server's relay is not published. The driver obtains its pin from the disposable server's generated **public** key through Docker; it does not learn trust from the first network connection. The system OpenSSH control uses a generated configuration, no agent and a strict fixture known-hosts file. Keys die with the temporary directory/container. Neither the desktop nor a player profile is started by this experiment.

The standalone [manifest](../../apps/desktop/src-tauri/tests/persistent-ssh/Cargo.toml) selects russh 0.65.4 with `ring`, not its defaults. The separate lock has 199 package/version pairs including the test binary; 129 of those pairs are absent from the desktop lock. Those are resolution counts across targets, not 129 extra shipping libraries. The desktop does not depend on this crate. Embedding it would move host-verification policy, signing-key custody and crypto updates into the application's maintenance boundary.

## Actual native traffic

Debug Rust 1.98.1 on Linux/WSL2 and native x64 Windows/MSVC. Both used the disposable Linux sshd, production relay, synthetic adapters and real WebSockets. The Windows host driver used the existing native Python 3.12.10 relay environment (Linux used 3.12.14); the Windows control was system OpenSSH 9.5p2, Linux 10.2p1. The controls and candidate ran on the same platform against the same backend, alternating first mover within a pair and across runs.

| Test boundary                                                                                                                                                   | Linux         | Native Windows |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | -------------- |
| Held state, exact matching action delivered once, wrong context refused, replacement consumer receives no replay                                                | Pass          | Pass           |
| Three authenticated candidate sessions, each carrying held state and 20 fresh action channels without another accepted login (server log); controls stayed warm | Pass          | Pass           |
| Client-first and server-first EOF with reverse bytes verified; simultaneous full-duplex transfer with digests                                                   | Pass          | Pass           |
| Sibling progress under upload backpressure; cancel while uploading; individual stream close and continued session use                                           | Pass          | Pass           |
| Sibling echo while a separate download reader was stalled                                                                                                       | **Timed out** | **Timed out**  |
| Cancel stalled download, recover held channel, refuse absent backend and use it again on same session                                                           | Pass          | Pass           |
| Graceful stop, stdin EOF, abrupt owner death with active channel; unrelated system SSH survives                                                                 | Pass          | Pass           |
| Missing pin, changed key, bad identity, server denying forwarding, occupied raw listener; no trust repair or listener adoption                                  | Pass          | Pass           |
| Authenticated session A lost during a dispatched action; invalidate, return unknown, observe no replay to B, recover only with fresh state                      | Pass          | Pass           |

Both drivers completed the independent real-relay matrix and returned **3** for the raw limitation. The test does not infer which part of an SSH channel open or echo was blocked. After resetting the stalled reader the retained channel worked again: isolation while stalled failed even though cancellation recovered. Raising a queue limit would not establish independent progress.

### Authority probes

- **Local port reuse:** a competing listener bound the released control port. Neither a delayed state retry nor an action reached it; Guided fixture traffic has no renderer-facing TCP listener. Attempts to start the raw test adapter on an occupied port failed without adoption.
- **Replacement:** with A's action upgrade deliberately stalled, revocation prevented dispatch. A delayed state subscription was rejected before opening B's channel. Relabeling A's context with B's actual generation did not admit an action before B's own state. A and B had coincident game-context values and separate native session handles.
- **Late events:** tests at the native gate rejected a retired snapshot and stale completion. On the wire, A's accepted dispatch was held while B took a new action; the old invocation ended unknown and B completed independently. A foreground change away and back also invalidated the old ticket. The late-snapshot check is a deterministic native test, not a claim that an old packet was observed arriving through the WebView.
- **Loss and shutdown:** killed the disposable sshd connection after the consumer took a command. The outstanding action stayed unknown even when that consumer later finished. The replacement consumer saw no automatic send. Shutdown with a pending action ended the process, retired its work and sent no queued post-shutdown action.
- **Counterexample within one generation:** stopped only A's remote acceptor, kept its established state stream alive, and put relay B on the same remote loopback port. A continued publishing valid updates. B registered the same exact game context and received the next action. The SSH server, session, destination and native generation had **not changed**. This is a successful test of a **failed attribution guarantee**; it is not an example of route A/B isolation.

### Performance

[Raw paired action times](persistent-ssh-actions.csv): 3 runs × 20 pairs per OS, 60 observations per method per OS. Pooled p50 is the median; p95 is nearest rank. The separate decision statistic uses paired differences, **not** the difference of pooled quantiles. The native timer includes the pipe request and returned result; the warm control uses the existing WebSocket driver. Each of the two retained subscribers also received all 40 expected post-action snapshots per run in sequence. This is evidence of progress during ordinary action traffic, not a separate state-render latency measurement.

| Platform | Warm -L p50/p95 (ms) | Persistent p50/p95 (ms) | Paired overhead p50 / allowed (ms) | Paired overhead p95 / allowed (ms) |
| -------- | -------------------: | ----------------------: | ---------------------------------: | ---------------------------------: |
| Linux    |          2.73 / 4.46 |            2.87 / 48.53 |                       +0.18 / 5.00 |                  **+45.75 / 5.00** |
| Windows  |        18.77 / 61.44 |            2.93 / 49.39 |                       −1.92 / 5.00 |                 **+43.05 / 15.36** |

Windows' candidate pooled p95 was lower than the control's. The candidate still had sufficiently many slow actions paired with fast controls to fail the predeclared p95 gate. Linux candidate per-run p95s were 48.53, 48.11 and 48.46 ms; Windows 48.6, 50.0 and 48.0 ms. No WAN inference or cause for the tail is claimed.

| Duration in ms (unless noted)                                                                        |          Linux |           Windows |
| ---------------------------------------------------------------------------------------------------- | -------------: | ----------------: |
| Cold public-key authentication call only / total SSH ready, separate 20-channel probe (one startup)  |  16.76 / 31.25 |     11.35 / 42.66 |
| Native authority establish, 3-run median / OpenSSH control authenticated listener setup, one startup | 33.10 / 121.55 |    32.50 / 113.21 |
| First decoded snapshot after ready, native / control, 3-run median                                   |  93.07 / 43.57 |     87.86 / 47.76 |
| SSH channel open confirmation, p50 / p95 (20)                                                        |    0.42 / 0.74 |       0.29 / 0.43 |
| Local EOF to explicit peer CLOSE, p50 / p95 (20)                                                     |    0.46 / 0.74 |       0.39 / 0.49 |
| Restore raw relay listener through successful echo / echo itself (one)                               |    5.21 / 1.50 |      13.29 / 2.34 |
| SSH failure injection to invalidation / to fresh replacement state and verified action (one)         | 74.06 / 518.93 |   104.14 / 562.81 |
| Graceful stop during blocked upload, local cleanup / observed remote application drain (one)         |   8.35 / 31.12 | 2055.65 / 2058.12 |
| Abrupt owner death to verified local and remote cleanup (one)                                        |          10.86 |           2712.32 |

The raw timing probe did not mistake `ChannelStream` drop for peer acknowledgment. It measured EOF to peer-initiated CLOSE; russh does not expose receipt of the peer's reply to a locally initiated CLOSE here. Control authentication was not separately instrumented from foreground OpenSSH setup. Loss/replacement and cleanup numbers include test control and observation, not just SSH wire latency.

| Held-state sample (3 per method) | Processes | Threads |            Resident KiB range |
| -------------------------------- | --------: | ------: | ----------------------------: |
| Linux warm / native              |     1 / 1 |   1 / 3 |         9,328 / 11,324–11,372 |
| Windows warm / native            |     1 / 1 |   4 / 7 | 11,368–11,380 / 11,332–11,340 |

One owner process, no SSH child for the candidate. The raw listener admits at most eight live forwards; the authority admits at most eight action jobs and one state reader, with bounded input, output and opening work. These process-tree samples exclude the Python driver, server and desktop; they are not peak allocations or a soak test.

## Decision boundary

Do not move this code or its fixture key loader into the desktop. Preserve existing system-OpenSSH Managed/External and Direct WSS behavior. Before another SSH-library trial, decide whether Guided must distinguish successive relay processes on the same authenticated host. If it must, the smallest meaningful next experiment is to bind state and action to one application connection or to a backend incarnation checked by the action receiver. Another client-side generation or faster SSH library cannot establish that property. This would require a separately approved application protocol experiment; none is implemented here.

Five isolated Rust tests passed on each native OS. Existing desktop Rust tests: Linux 94 passed and two ignored; Windows 96 passed and two ignored. Linux desktop Clippy and strict experimental Clippy on both platforms passed. Windows desktop Clippy retained its three existing `unnecessary_cast` warnings in `topmost.rs`. The authenticated legacy Managed normal/hard/relaunch, proxy/jump and external-master regression passed with the existing frozen node and harness in a disposable Linux network namespace; an occupied host port was left untouched.

No production WebView-to-native handoff, trust migration, SSH agent, encrypted player key, SSH certificate, alias/jump compatibility, installed Guided route, macOS, WAN, MUD or independent player acceptance is claimed. Production UI, persisted configuration, Direct credentials, relay protocol and installed routes are unchanged.
