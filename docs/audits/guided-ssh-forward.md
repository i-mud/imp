# Slice 29.3a: native Guided SSH forwarding experiment

This is an acceptance harness, not a desktop transport. Neither this branch nor
[v0.6.0](https://github.com/i-mud/imp/releases/tag/v0.6.0) offers a Guided SSH
route. There is no new Tauri command, saved profile, credential, or renderer URL.
The existing Managed alias and External procedures still apply.

## Why try a different forward?

At the v0.6.0 baseline (`3a8b0c7`), the desktop Managed supervisor probes port
8789, adopts a compatible listener or starts OpenSSH with `-L`, and waits for an
open port while its child is alive. On child exit it retries; on application
exit it stops only what it owns. The renderer is independently given that port
for `/state` and `/action`. A successful `/healthz` response proves neither the
identity of the listener's owner nor its SSH destination. Polling a readiness
flag before opening a WebSocket would leave a check-to-connect race.

The candidate binds one IPv4 loopback socket itself. It does not find a free
port and then hand it to SSH. Each accepted connection gets a separate system
OpenSSH process: `-W 127.0.0.1:8787` connects its stdin and stdout to the
server's loopback relay. OpenSSH never owns the local listening socket and never
executes a remote command. Closing one stream or failing its login must not
release the listener, attach to an existing forward, or retry its application
bytes. A new connection starts a new SSH authentication. This is deliberately
not the persistent session used by `-L`.

The harness admits at most eight active streams. Each owns its child tree and
two data pipes; SSH diagnostics are drained without being printed. A child's
stdin EOF is not the Unix guardian's lifetime signal. The Windows listener
requests exclusive use before binding. Orderly cancellation stops admission,
retires workers and then releases the listener. Hard process death relies on
OS handle closure/containment; it does not provide that orderly sequence.

## Decision: do not adopt per-connection SSH for gameplay

Both native clients reached the actual relay and sustained a state WebSocket
while sending 20 sequential, exact-context actions. The candidate was slower on
every platform. Even on loopback, the Windows median action took 161 ms rather
than 22 ms. It also lost the reverse half-close that the warm `-L` preserved.
This is not a suitable transparent replacement for the existing transport.

One run per platform against disposable Linux sshd, debug harness, generated
Ed25519 key, no external network on the data path. Linux used OpenSSH 10.2p1
under WSL2; native Windows used system OpenSSH 9.5p2. Action times start at
WebSocket connection initiation and end at `action-result`, **before** close.
Snapshot times start after local listener readiness. Recovery is a fresh
short-stream round trip after restoring the backend on the same port.

| Client  | Forward   | Local ready (ms) | First snapshot (ms) | Action p50 (ms) | Action p95 (ms) | Recovery (ms) |
| ------- | --------- | ---------------: | ------------------: | --------------: | --------------: | ------------: |
| Linux   | warm `-L` |            101.4 |                49.4 |             2.6 |             3.5 |           1.2 |
| Linux   | new `-W`  |             20.7 |               182.7 |           154.4 |           161.1 |         147.8 |
| Windows | warm `-L` |            160.2 |                44.5 |            22.3 |            51.1 |           2.6 |
| Windows | new `-W`  |             35.3 |               255.7 |           161.1 |           174.6 |         170.3 |

`-W` appears ready earlier only because it has **not authenticated yet**. The
warm `-L` baseline uses a single foreground OpenSSH process; the candidate
starts another SSH session for every action. With state and a second stream
held simultaneously, the sampled local trees were:

| Client  | Forward   | Processes | Threads | Resident KiB |
| ------- | --------- | --------: | ------: | -----------: |
| Linux   | warm `-L` |         1 |       1 |        9,416 |
| Linux   | new `-W`  |         5 |      14 |       26,736 |
| Windows | warm `-L` |         1 |       4 |       11,704 |
| Windows | new `-W`  |         5 |      27 |       44,052 |

Counts include the candidate harness and descendants, not the HUD, server, or
Python driver. These are sampled resident sets (shared pages can be counted
twice), not peak or unique bytes. The SSH sample is not a WAN benchmark or a
player study. Both fit the HUD's five-second action deadline in this fixture;
meeting a timeout is not the same as acceptable repeated action latency.

**What worked:** continuous exclusive listener ownership during startup, relay
failure and recovery; competing binds refused; no adoption of an occupied
port. Client-first EOF, full-duplex traffic and independent traffic under
upload/download backpressure worked. Stop, stdin EOF and abrupt parent death
released the listener and sampled children. The unrelated warm SSH connection
and separate listener survived. Both clients stopped for unknown/changed host
keys, an unauthorized identity, forwarding denial and an absent backend.

**What failed:** after the server sent its final bytes and closed only its
write half, it did not receive the client's subsequent upload intact through
the candidate. The same digest check passed over `-L`. Do not describe `-W` as
a general TCP proxy on the strength of the successful WebSocket tests.
The fixture finishes the remaining cleanup and isolation checks before returning
exit code `3` for this candidate limitation. It is not a green transport gate.

Keep `-W` under consideration for an isolated one-shot relay check, not as an
approved runtime. Compare a single persistent SSH session with private,
authenticated native channel access before choosing another runtime design.
That alternative must prove endpoint attribution on Windows as well as Unix;
do not assume a portable control socket or fall back to adopting port 8789.

## Trust and identity

The experiment requires an established host key and noninteractive access. Its
host, user and optional port are separate arguments. A supplied user and port
override matching configuration; an omitted port inherits OpenSSH configuration
(default 22). A host label is an OpenSSH selection input, not a guarantee about
the final address: trusted configuration can apply `HostName`, `Match`,
identities, certificates, proxies and jump hosts. Evaluating that configuration
can run local code. No automatic `ssh -G` evaluation, host-key repair, password
collection or key deployment is part of the experiment. The harness takes an
isolated configuration file instead of using a player's SSH files.

Destination options are not a policy for independently launched proxy or jump
processes. Those require their own verified hosts, noninteractive settings and
foreground lifetime; see the [existing proxy limitations](../architecture/processes/managed-runtime.md#desktop-tunnel-boundary).
An independent session for an action can also resolve to a different backend
than the session that supplied its state, even when both SSH logins succeed.

## What a production design would still have to prove

- While the listener is held, its bytes can be tied to the native bridge. After
  teardown the port number is reusable. An old renderer retry or a late action
  that connects by number alone can reach a different process. A status event,
  a healthy relay, or merely choosing an ephemeral port does not fix this.
- State admission, reconnect, and every one-shot action must share a revocable
  route generation. Revocation must prevent new work, close the old channels,
  invalidate actionable context and ignore late completions. A command with an
  unknown delivery result must never be replayed.
- Binding to loopback protects against direct network access, not other local
  processes. Nor does an authenticated SSH destination prove fresh game data
  or identify the intended character.

No player steps were removed in 29.3a: the terminal preflight, alias setup,
manager decisions, save and restart remain as in v0.6.0. Installed regression
checks of that workflow are not acceptance of a guided route. Published-artifact
acceptance and independent player onboarding remain separate.

## Reproducing the comparison

Use the platform's native Rust toolchain, system OpenSSH client, Docker running
Linux containers, and the existing relay development environment. On Windows
use the [native mirror](../development.md#canonical-checkout-and-windows-native-mirror)
and append `.exe` to the harness path below. Run both commands from the
repository root:

```sh
cargo build --locked --manifest-path apps/desktop/src-tauri/Cargo.toml --features runtime-acceptance --bin runtime-ownership-harness
uv run --locked --project services/relay python scripts/run-guided-forward-fixture.py apps/desktop/src-tauri/target/debug/runtime-ownership-harness
```

The Docker runner creates an ephemeral Linux account and host, allowed and
forwarding-denied keys, plus a synthetic controller. It publishes SSH and the
test controller only on randomly assigned loopback ports. The real relay runs
on `127.0.0.1:8787` **inside** the container; it is never published.
`MaxSessions 0` prohibits remote shell/command sessions even for the allowed key. An initial
probe holds the real `/state` WebSocket open while exercising separate `/action`
WebSockets with a synthetic producer and consumer. The raw stream measurements
use a separate synthetic backend, not a MUD or user-visible HUD timer.
