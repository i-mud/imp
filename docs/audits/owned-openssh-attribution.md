# Owned OpenSSH stream and relay attribution (Slice 29.3d)

**Gate 1: Fail on native Windows. Stop before Gate 2.** Exact established-stream attribution worked and met the practical screen on both platforms. What failed was the separate requirement to **refuse a conflicting listener**. Two foreground Windows OpenSSH forwards remained bound to the same IPv4 loopback address and port. One owner could still prove a stream while both listened. No application bytes to the wrong owner were observed. A fast or correctly attributed individual connection does not resolve the bind conflict.

## Question and scope

Can a native client prove where **its already-connected stream** goes before it writes the HTTP upgrade? Only if that works on both supported platforms is it worth testing whether the receiver of a separately opened action is the relay incarnation that published the displayed state. The two identities are independent. In [the previous experiment](persistent-ssh-authority.md#authority-probes), the authenticated SSH session and exact game context stayed the same while the remote action receiver changed.

Baseline: `ca445d4c3bb853b7bc4cf54685e2ef3ae3e12ecd`, matching the clean checkout and the live `main` branch on `origin`; latest published release [v0.6.0](https://github.com/i-mud/imp/releases/tag/v0.6.0), commit `3a8b0c77f88a03fb41ebe908b402476c4e5dc0ee`. The experiment uses generated fixture credentials, an independently supplied fixture server key and system OpenSSH. It does not change the player’s SSH files, create accounts on a player host, execute a remote SSH command, expose the relay or change the [host-local trust boundary](../architecture/boundaries/trust-boundary.md#host-local-trust-model).

## Gate 1: what counts as evidence

The guarantee under test is **no application bytes and no admitted state through an unproven local stream**. It is not a promise that another listener cannot observe a TCP handshake. A failed dial, a dead child, refusal of ownership evidence and failure to reach the remote relay are distinct outcomes. A free-port probe, listening PID, process name, successful health check or live SSH child alone cannot authorize any subsequent socket.

Keep the original IPv4 loopback `TcpStream` open from connect through proof to WebSocket upgrade. Match its reversed address/port pair on the server side. Do not use an HTTP client that can redial or follow a redirect. Closing the SSH child after proof can break that connection; rebinding its listening port does not retarget the existing stream. Retirement must block a new dial itself, not merely discard its eventual result.

Windows [IP Helper](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcprow_owner_pid) describes an established connection’s PID as the issuer of its **context bind**. This is not a notification that userspace has returned from `accept()`. A non-elevated native Windows control held a client open and withheld `AcceptTcpClient`: `Get-NetTCPConnection` already returned one reversed ESTABLISHED row with the fixture PID, and returned the same owner after accept. The claimed endpoint association must therefore depend on the exact held stream, the stock foreground process and the retained process handle, not on a supposed observation of application receipt. Do not extrapolate it to arbitrary socket duplication or intentional transfer between processes.

## Predeclared practical screen

On each native client, alternate strict and no-attribution trials on the same already-authenticated stock `-L` forward, 60 pairs for state setup and 60 for one-shot actions. Compare the median and nearest-rank p95 of **paired latency differences** with the larger of 5 ms and 25% of the corresponding warm-control quantile. The floor allows short local scheduling jitter; the relative term scales with the slower control. Record the actual post-connect proof interval and observation count separately so that a slow WebSocket round trip is not mislabeled as slow ownership lookup. Allow at most 100 ms for evidence and no unexplained valid-stream refusals. This deadline is a rejection cap, not a gameplay latency target. A finite run is neither a reliability bound nor a proof of race freedom.

## Native mechanisms

The [standalone crate](../../apps/desktop/src-tauri/tests/owned-forward/Cargo.toml) includes the desktop runtime owner source, not the Managed supervisor’s port adoption or retry logic. Each owner starts one foreground `ssh -N -T -S none` with `ExitOnForwardFailure=yes`, `ForkAfterAuthentication=no`, `BatchMode=yes` and strict host verification. `-l`, optional `-p`, host and `-L 127.0.0.1:<local>:127.0.0.1:<remote>` are separate arguments. Choosing a disposable port does **not** reserve it for SSH. The fixture supplies its own `-F` file, public host pin from the Docker control plane and generated identity; the SSH server permits local forwarding but no command sessions. Nested proxy/jump processes would need their own trusted, noninteractive foreground configuration. This is not a test of arbitrary player configuration.

- **Linux:** the guardian reports its direct child PID. Waiting on the guardian alone does not rule out that child exiting. A fixture bootstrap acknowledges over inherited pipes **after** the owner obtains a [pidfd](https://man7.org/linux/man-pages/man2/pidfd_open.2.html), then `exec`s stock SSH without changing process identity. The owner checks the pidfd and guardian, enumerates socket descriptors in `/proc/<ssh-pid>/fd`, and compares device and inode with the [duplicated descriptor](https://man7.org/linux/man-pages/man2/pidfd_getfd.2.html). It checks TCP protocol/state, reverse tuple, network namespace and no device binding. It pins the match and rechecks the descriptor and socket cookie before approving the held client socket. A vanished slot is not evidence. Duplicate matches, denied access, inconsistent identity or expiration do not authorize a write. Tested as UID 1000, no effective capabilities, ordinary procfs, Yama `ptrace_scope=1`. More restrictive ptrace/seccomp/procfs configurations can deny the operation. No elevation or permission fallback was used. Production would need an equivalent birth-bound child identity; the current guardian API does not hand the desktop a pidfd.
- **Windows:** the existing runtime creates stock SSH in a private kill-on-close Job Object and retains its process handle. `GetExtendedTcpTable(AF_INET, TCP_TABLE_OWNER_PID_CONNECTIONS)` must return a unique ESTABLISHED reversed tuple attributed to that child. The retained-handle wait must report it alive before and after the query; reopening a process by PID would not suffice. Table sizing/growth is bounded. An unexpected PID, ambiguous row, query failure or late result does not authorize an upgrade. The native delayed-accept control also exercised this API directly. The snapshot is not a lock on the listening port.

Both versions carry the same `TcpStream` into `tungstenite::client_with_config`. A missing row can be observed again within the deadline; a failed proof drops the pending stream. The route has no background reconnect. The warm benchmark bypass is available only in a separate control owner after its first strict upgrade. It is not a recovery path after a failed strict request.

### Why contention changes the decision

On Linux one contender exited, the winner admitted real relay state, and a fresh owner could later use the released port. On native Windows the full fixture timed out after eight seconds waiting for one contender to exit. A timeout alone would not prove a shared bind. In a focused native run, `netstat.exe -ano -p tcp` showed one listening row **per retained fixture SSH PID** (31364 and 80072) on `127.0.0.1:54828`. Neither child had been stopped. The first owner’s held stream returned `proof_failed`; the second owner’s held stream passed on its first observation. The fixture closed both without upgrading, and the relay’s upgrade count did not change.

A separate focused run using `Get-NetTCPConnection` reported only one of the two competing PIDs on its queried port. Do not use that single-row display to infer exclusivity. Microsoft [documents the distinction between address reuse and exclusive use](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse). `ExitOnForwardFailure=yes` cannot turn an OS-successful bind into an exclusive claim. An earlier probe of a free port would not close this race either. This is evidence of simultaneous listening, **not** evidence that rebinding transfers a previously established TCP stream.

## Acceptance on native clients

Linux was WSL2 with system OpenSSH 10.2p1. Windows was a non-elevated native x64 process on build 26300, using system OpenSSH 9.5p2. Both used the actual relay and synthetic adapter through a disposable Linux sshd. Host `8787` and `8789` were not used for the experiment. The two installed transports and the renderer were not run through the experimental route.

| Scenario                                                                                                                            | Linux                                                        | Native Windows                                                   |
| ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------- |
| Normal forward, held `/state`, 20 independently proven one-shot fixture actions, continuing exact snapshots; wrong context rejected | Pass                                                         | Pass                                                             |
| Healthy local relay already bound; unrelated listener already bound                                                                 | Refused both                                                 | Refused both with exclusive fixture listeners                    |
| Simultaneous stock OpenSSH owners on one disposable port                                                                            | One winner; loser refused; fresh owner subsequently admitted | **Failed sole-owner screen**                                     |
| Child exits between last preconnect check and TCP connect                                                                           | One bare accept; zero bytes                                  | One bare accept; zero bytes                                      |
| Child exits after connect, before proof; child exits after proof, before upgrade                                                    | Both refused                                                 | Both refused                                                     |
| Relay-shaped replacement after stop or retirement                                                                                   | No TCP dial to replacement, bytes, upgrade or action         | Same                                                             |
| Delayed work presented to retired route                                                                                             | No new dial                                                  | No new dial                                                      |
| Missing ownership evidence; contradictory socket observations                                                                       | No approval; native identity/socket tests                    | No approval; native duplicate, wrong-owner and missing-row tests |
| Unknown host key, changed host key, unauthorized identity                                                                           | All refused                                                  | All refused                                                      |
| Forwarding denied; remote relay absent                                                                                              | No upgrade or snapshot; proof timed out                      | No snapshot; local proof succeeded but upgrade failed            |
| Graceful stop, abrupt owner death, independently owned SSH control                                                                  | Resources released; control survived                         | Resources released; control survived                             |

The [fixture driver](../../scripts/run-owned-forward-fixture.py) stops and reaps only its own child at the barriers. The passive listener counts accepted TCP connections and received bytes, and checks for an HTTP upgrade. With zero bytes, zero action frames follows directly. The genuine local relay separately observes received bytes, HTTP upgrade attempts and decoded actions. It serves a health/control request independently of the refusal measurement. Port reuse did not turn proof for the old stream into proof for a new one. These are bounded observations, not a stress-test proof of race freedom; real PID exhaustion/reuse was not induced.

## Incremental cost

Milliseconds except for counts; negative paired differences mean the strict trial happened to finish sooner. End-to-end state timing ends at the retained snapshot; action timing ends at the result, not socket close. No authentication startup or control-pipe round trip is charged to either.

| Platform / workload | Warm median / p95 | Strict median / p95 | Paired overhead median / p95 | Allowed median / p95 | Post-connect proof median / p95 / max | Observations per proof |
| ------------------- | ----------------: | ------------------: | ---------------------------: | -------------------: | ------------------------------------: | ---------------------: |
| Linux state         |     44.36 / 47.86 |       47.22 / 50.68 |                +0.06 / +6.80 |        11.09 / 11.97 |                 1.170 / 1.228 / 1.294 |                      2 |
| Linux action        |       1.88 / 2.16 |         2.84 / 3.49 |                +0.97 / +1.54 |          5.00 / 5.00 |                 1.158 / 1.183 / 1.216 |                      2 |
| Windows state       |    93.14 / 111.88 |      92.87 / 113.17 |               −0.64 / +17.19 |        23.29 / 27.97 |               0.460 / 14.621 / 14.927 |                      1 |
| Windows action      |   120.31 / 137.70 |     121.43 / 165.92 |               +0.80 / +22.45 |        30.08 / 34.42 |               0.464 / 10.578 / 25.340 |                      1 |

Each cell completed 60 pairs with zero proof timeouts or unexpected valid-stream refusals. Linux needed a second observation after a bounded sleep in every timed trial. Windows took one observation throughout, including its slower samples. The end-to-end tail is not solely proof cost; no scheduler or API-internal cause is assigned. These local debug-harness figures are not WAN or player latency predictions.

With held state, the sampled Linux tree had 3 processes, 5 threads and 17,088 KiB resident; Windows had 3 processes, 12 threads and 26,724 KiB. Each contained one SSH client. Eleven Linux and nine Windows cleanup observations in the full runs found none of the tracked process identities remaining, the listener closed and its port bindable. These are sampled trees, not peak memory, a soak test or evidence about independently daemonized proxies.

## Reproduction

Use Docker with Linux containers, system OpenSSH, and the relay development environment. On Windows, use the [native mirror and toolchain](../development.md#canonical-checkout-and-windows-native-mirror) and the native relay Python, not a cross-compiled binary or Windows SSH invoked by a Linux test client.

```sh
cargo build --locked --manifest-path apps/desktop/src-tauri/tests/owned-forward/Cargo.toml
uv run --locked --project services/relay python -u scripts/run-owned-forward-fixture.py apps/desktop/src-tauri/tests/owned-forward/target/debug/owned-forward
```

```powershell
$m = 'apps/desktop/src-tauri/tests/owned-forward'
cargo build --locked --manifest-path "$m/Cargo.toml"
& services/relay/.venv/Scripts/python.exe -u scripts/run-owned-forward-fixture.py "$m/target/debug/owned-forward.exe"
```

Add `--contention-only` to isolate the bind decision. The full driver reports each case independently; a failed screen returns nonzero. Container control operations provision only disposable resources. Neither real MUD commands nor changes to user SSH files are part of these procedures.

## Stop decision and retained limits

Do not implement strict Guided gameplay from this candidate. Keep Managed alias/adoption, External SSH, Local and Direct WSS unchanged. Better Managed onboarding is a separate product decision, not a fallback authorized by this test.

Gate 2 was not authorized by the result. There is no fixture incarnation, bound action path, downgrade fallback, broker change, or new result for the PR #70 remote-listener crossover or actual v0.6.0 downgrade. Local ownership cannot distinguish two remote relay incarnations on one SSH session. The WebView still has no implemented authority handoff to these native streams. The fixture proves permanent retirement of an owner, not same-process replacement, foreground revision fencing, late invocation cleanup or no-replay across relay replacement. `forwarded` remains adapter acceptance, not proof of MUD execution. No production code or protocol changed.

Verification included experimental Rust formatting, tests and strict Clippy on both native toolchains (six tests passed per OS; Linux also had an ignored guardian helper). The existing native `runtime_ownership` regression passed on each OS (12 passed; Linux also had one ignored helper). The existing authenticated Managed SSH regression passed using its frozen node inside an isolated WSL network namespace. The experimental Windows matrix intentionally remains nonzero; it is independent of the normal project checks.
