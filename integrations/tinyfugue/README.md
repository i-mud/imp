# TinyFugue adapter

Imp keeps MUD-specific data outside the desktop application:

```text
MUD GMCP -> TinyFugue hook -> private spool -> imp-feed
                                                   |
                                                   v
                                        relay 127.0.0.1:8787
                                          |               |
                                   SSH local forward       +-> gateway 127.0.0.1:8788
                                          |                         |
                                          |                       TLS/WSS
                                          +---------> desktop HUD <-+
```

`imp-feed` and `imp-action-consumer` accept loopback relay URLs only,
and both the relay and authenticated gateway listen only on loopback. Loopback
prevents remote network access but does not enforce OS-user ownership. Any
process in the VPS network namespace, including another local user, can reach
the relay's local endpoints.

SSH transport forwards the relay's entire TCP port to the workstation,
including producer and action-consumer routes, without application
authentication. The forwarded local listener therefore has the same host-local
trust requirement. Direct WSS instead authenticates one pairing token at the
separate gateway before the gateway opens a relay connection. It exposes only
state and action routes (and optionally health), not the relay's producer or
action-consumer routes. That is transport authentication, not per-user
authorization. Browser `Origin` checks remain defense-in-depth rather than
authentication.

Supported deployment therefore requires a single-user workstation and VPS, or
mutual trust among host-local users and processes. An untrusted multi-user host
is out of scope. The relay and gateway have no non-loopback bind override.

## Versioned spool event contract

The TF-facing hook emits one UTF-8 `IMP2` event per line:

```text
IMP2 G <session> <connection> <world-token> <epoch-seconds> <package> [JSON]
IMP2 T <session> <connection> <world-token> <epoch-seconds> <text-token>
IMP2 R <session> <connection> <world-token> <epoch-seconds>
IMP2 S <session> <foreground> <connection> <world-token-or-> <epoch-seconds>
```

- `session` is stable for one TinyFugue process.
- `foreground` increases when the selected world changes.
- `connection` increases whenever a world connection is reset or reconnects.
- `world-token` is TinyFugue `textencode.tf` data, not executable TF source.
- `G` carries GMCP, `R` resets only that world's accumulated state, and `S`
  selects the only world whose state may be published. `-` means no selection.
- `T` carries one normal received MUD line as `textencode.tf` data. It is
  bounded at capture, accepted only for the selected exact connection, sent
  best-effort, never checkpointed, and never replayed after a relay outage.

The parser rejects malformed versions, fields, encodings, timestamps, JSON, and
oversize records without logging raw content. Each world has an independent
normalizer and checkpoint entry. Background-world GMCP updates only that
world's cache; it cannot overwrite the foreground snapshot. A changed session
clears every prior world.

The normalized runtime checkpoint is versioned, session-bound, per-world, and
ephemeral under `$XDG_RUNTIME_DIR/imp/state.json`. It is never raw GMCP or
durable session history. The exact active context is also written atomically as
`IMPCTX 2 <session> <foreground> <connection>` to the private `0600`
`~/.local/state/imp/context` marker.

## Running (normal production path)

Copy the unchanged hook to `~/.config/imp/capture.tf`, then add this line
to the startup file used by the operator's actual TinyFugue invocation:

```text
/load ~/.config/imp/capture.tf
```

TinyFugue's `-f FILE` option loads `FILE` instead of the normal personal
config. For example, when starting from `~/avatar/tf` with
`tf -f./.tfrc -n`, the load belongs in `~/avatar/tf/.tfrc`, not an assumed
`~/.tfrc`. Load it before any world connects or logs in. The hook is additive:
it does not replace the operator's GMCP negotiation or connection macros and
sends no GMCP of its own. Installation must not create or overwrite an
operator startup file. See [`../../deploy/README.md`](../../deploy/README.md)
for the VPS systemd setup.

The hook writes to the fixed path `~/.local/state/imp/spool`.
`imp-feed` creates that path as a symlink into its private runtime
directory and owns everything downstream:

```sh
# Terminal 1
uv run --directory services/relay imp-relay

# Terminal 2
uv run --directory integrations/tinyfugue imp-feed
```

`imp-feed` acquires the single-producer lock, creates the private drained
spool, parses versioned events, keeps normalization isolated per world, and
publishes only the selected exact context. A newer delivery cancels an older
pending publish so reconnect backoff cannot replay stale foreground state. On a
publisher reconnect it sends `select` again before any `publish`.

The TF hook starts an asynchronous `imp-action-consumer` for the selected
connection. It registers only while the feed's private context marker matches
and reconnects after an idle relay restart only while that marker remains
exact. While idle, it writes no probe bytes: it polls its stdout descriptor only
for terminal reader-loss events and exits without a result or reconnect when
TinyFugue closes the quote pipe. Each registration accepts at most one dispatch.
Action text arrives as WebSocket data, is converted to a `textencode.tf` token,
and is written as one fixed `/imp_send <session> <foreground> <connection>
<world-token> <encoded-data>` line. After that write and flush the helper reports
`forwarded` and exits, closing the shell-quote pipe instead of waiting for
another action. At execution, `/imp_send` rechecks the current TF session,
foreground generation, selected and quote-pinned world, and that world's
connection generation before calling `send()` and starting one replacement
helper. A stale line fails that same fence and cannot start a helper for its
obsolete context. The raw command never appears in shell argv or evaluated TF
source. `forwarded` does not mean that the local fence passed, `send()`
succeeded, the MUD socket received the command, or the MUD executed it. A pipe
failure closes the consumer so the relay reports `unknown`, and neither side
retries the dispatch.

To stop the feed without closing TinyFugue, stop `imp-feed` (or
`systemctl --user stop imp-feed.service`). The hook keeps trying the fixed
spool path and loses updates rather than blocking. Reloading or exiting
TinyFugue removes its session-local hook state.

## Identity bootstrap prerequisite

AVATAR sends the full identity-bearing `Char.Status` only once, during initial
character login, and documents no way to request another full snapshot; later
`Char.Status` messages are deltas that may omit `character_name`. Capture of
that single message therefore depends on the operator's TinyFugue sequencing
GMCP login at the right negotiation point.

The invariant is a TinyFugue build whose GMCP support includes the
`GMCP_LOGIN` hook, with operator login scripts that use it to run their GMCP
capability negotiation and send `Char.Login`. Without `GMCP_LOGIN` the operator
login path cannot be relied on to sequence this correctly: Imp keeps
normalizing `Char.Vitals` and `Char.Status` deltas but never observes identity,
so no state is published.

State it as a capability rather than a version: the build verified live was
`5.2.2-3-g4f0ff34`, but an upstream or distribution version number does not by
itself prove `GMCP_LOGIN` is compiled into the binary in use. The conclusive
signal is that login produces a full `Char.Status` carrying `character_name`
and Imp publishes a snapshot and writes its checkpoint. The verified run
observed `Char.StatusVars` immediately followed by that full `Char.Status`;
AVATAR does not document the ordering as a guarantee, so it is an observation,
not a protocol requirement Imp relies on - the normalizer accumulates
packages in whatever order they arrive.

Imp does not work around a missing identity: it never infers the local
character from `Room.Players` or `Char.Group.List`, never persists identity
across a reboot, and implements no `Char.Status` refresh request.

Even with those prerequisites, a very fast AVATAR login/world transition can
still be followed only by delta packets that omit `character_name`. Imp then
has no authoritative identity to publish. This known reacquisition limitation is
deferred; it is not attributed to the configurable action UI.

## Live feed transport: a drained spool, not a FIFO

TinyFugue's `fwrite()` is `fopen(path, "a")`, one write, `fclose()` - a fresh,
blocking open/write/close on every hook call, with no non-blocking option.
Measured against the real TF 5.1.6-4-ga15a165 binary:

| Target                                                       | Result                                                                                             |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| FIFO, no reader attached                                     | `fwrite()` blocked TinyFugue for the full call; killing the process was the only way to unblock it |
| FIFO, a reader that stops reading once the pipe buffer fills | `fwrite()` blocked TinyFugue indefinitely                                                          |
| Regular file, reader absent or slow                          | `fwrite()` returned immediately every time; TinyFugue never paused                                 |
| Regular file with its directory removed                      | `fwrite()` failed and printed one error line; TinyFugue continued in the same call                 |

A FIFO on the TinyFugue-facing hop can freeze the MUD client, which this
project's fail-open requirement forbids, and TinyFugue's `fwrite()` gives no
way to harden that: it is not Imp code and offers no non-blocking mode.
The live transport is therefore a private regular file that
`imp-feed` continuously drains. Once a fully drained generation crosses
64 KiB, the reader renames that inode to `spool.retired` and creates a fresh
active spool instead of truncating an inode TinyFugue may already have open.
The retired generation remains readable long enough to collect any append that
raced the rename, then is removed after the next active generation reaches the
rotation threshold. At most two bounded runtime generations are retained.

`fopen(path, "a")` reopens the stable hook path on every call, so the next
TinyFugue event follows the current spool generation without a TinyFugue
restart. When the feed stops, the hook-facing symlink is removed; the systemd
unit also removes it after abnormal service termination, so feed absence drops
updates instead of accumulating an unbounded raw file.

`imp-bridge --fifo` is unrelated to this hop and unchanged: it reads
already-normalized adapter records (not raw hook output) from a FIFO some
other producer writes to, for cases such as feeding pre-converted records
into a relay by hand. Its writer is always Imp-controlled code, not
TinyFugue, so the blocking-writer problem above does not apply to it.

## Diagnostic raw capture (opt-in)

Normal operation retains raw GMCP only in the private, bounded,
ephemeral live spool. It does not retain raw diagnostic history.
`imp-feed --diagnostic-capture` writes raw non-text hook lines, unparsed,
to a private, size-rotated file set. Transient `IMP2 T` received-text events are
explicitly excluded even when diagnostic capture is enabled. The files live under `~/.local/state/imp/diagnostics/` (mode `0700` directory, `0600`
files, 1 MiB per file, 5 files kept). This is a debugging aid for a specific
session, not a default; see
[`../../deploy/README.md`](../../deploy/README.md) for enabling it under the
VPS systemd deployment.

## Offline conversion and replay

Convert an existing raw capture or replay adapter JSONL without a relay.
Converted records are unredacted MUD data, so write them outside the
repository:

```sh
uv run --directory integrations/tinyfugue imp-capture \
  "$HOME/.local/state/imp/diagnostics/gmcp.raw" \
  --output "$HOME/.local/state/imp/records.jsonl"
uv run --directory integrations/tinyfugue python -m imp_tf.replay \
  fixtures/real-session.jsonl --dry-run
```

`--dry-run` prints each changed, complete normalized state as protocol JSON
and opens no socket. A live replay URL must remain loopback and may use
`--interval` for pacing.

Network replay is display-only: every replayed state is selected with a null
context, so it cannot authorize an outbound action.

## Nondisruptive live verification

These checks observe context selection without sending an outbound action or
changing operator login/GMCP macros:

1. Install the updated `capture.tf`, start or restart the relay and feed, then
   `/load ~/.config/imp/capture.tf` before connecting worlds.
2. Select one connected world normally. Check
   `stat -c '%a %n' ~/.local/state/imp/context`; it must be mode `600`.
   `cat ~/.local/state/imp/context` must show exactly one newline-terminated
   `IMPCTX 2` record.
3. Record `curl -s http://127.0.0.1:8787/healthz` and the HUD identity/vitals.
   Normal foreground GMCP should move the feed to `live`.
4. Switch to another already-connected world without disconnecting either one.
   The marker's foreground generation must increase and the HUD must show only
   the selected world's cached or subsequent state. Background activity must
   not overwrite it.
5. Reconnect the selected world. Its connection generation must increase, the
   selected snapshot must reset rather than reuse the old generation, and only
   fresh GMCP for the new generation may restore `live`.
6. Switch back and forth once more, then inspect
   `$XDG_RUNTIME_DIR/imp/state.json`: it must be version 2, carry the
   current session, and keep separate world entries.

Stop if any tuple regresses, if a background world appears in the HUD, or if
TinyFugue pauses. Do not open `/action` during this checklist. A real outbound
command is a separate, explicit operator acceptance check because even a
read-only-looking MUD command is an external effect.

### Transient received-text acceptance

Status: **live-verified** on the VPS with TinyFugue
`5.2.2-3-g4f0ff34`, pinned to
`4f0ff34145b7c3f23e6233874d45ee102d98d9e9`.

The recorded run established the transient received-text boundary:

- With the feed temporarily suspended, a normal received line was present in
  the private spool as one `IMP2 T` event with `textencode.tf` payload data,
  proving the TinyFugue trigger-to-spool boundary independently of downstream
  delivery.
- With the real connected `musa` world selected, normal AVATAR output reached a
  live `/state` subscriber as `text` messages carrying the exact active
  `(session, foreground, connection)` context.
- While `musa` remained selected, an echo line was actually received by a
  background `-e` connectionless world and was visible in that world's
  TinyFugue history, but no matching `text` message reached the `/state`
  subscriber. This live-verifies the selected-world fence.
- After the original subscriber disconnected, a newly connected `/state`
  subscriber observed none of the previously delivered unique text markers;
  the recorded result was `REPLAY_COUNT=0`. Text is therefore not retained or
  replayed to later subscribers.
- Connectionless echo worlds are not valid foreground selected-context probes
  for this path because Imp selection requires TinyFugue
  `is_connected()`. They remain useful for proving a real background received
  line without sending traffic to a MUD.

This acceptance exercises TinyFugue capture, the drained spool, feed context
fencing, transient publisher behavior, relay broadcast, and subscriber
delivery. It does not claim native desktop alert matching; that is a separate
consumer of the transient `SourceEvent` boundary.

### Connectionless outbound acceptance check

Status: **live-verified** in a fresh operator process on TinyFugue
`5.2.2-3-g4f0ff34`, pinned to
`4f0ff34145b7c3f23e6233874d45ee102d98d9e9`. The recorded run used only
connectionless echo worlds: it verifies the local bridge, synchronous fences,
and helper lifecycle, not real MUD-server execution.

This procedure uses only TinyFugue connectionless echo worlds. No world has a
host or port, so no MUD can receive the test data. On the VPS, load the shipped
hook and create two local echo worlds:

```text
/load ~/.config/imp/capture.tf
/addworld -e ImpA
/addworld -e ImpB
/world ImpA
```

Start the normal relay, feed, SSH forward, and desktop development runtime with
`VITE_IMP_SOURCE=relay`. In the desktop browser's developer console, construct
the runtime clients through the same async factory used by application
initialization, and use its returned action sink; do not construct an `/action`
frame or open a WebSocket by hand:

```js
const { actionSink } = await import('/src/lib/config.ts').then(({ createRuntimeClients }) =>
  createRuntimeClients(),
);
const send = (context, command) => actionSink.send(context, command);
```

`createRuntimeClients()` loads the configured connection settings
and creates both source and action sink. The action sink opens and closes its
WebSocket per `send()`; it has no separate lifecycle cleanup to call. This
console-created runtime does not attach the state source, which remains
connected through normal application initialization.

Copy the exact `session`, `foreground`, and `connection` values from the
newline-terminated `~/.local/state/imp/context` marker into a JavaScript
context object. Use a unique literal command containing every parser-sensitive
form:

```js
const literal = '/#7 say "100% ready"; /look';
await send(contextA, literal);
```

Before sending actions, establish A and B once, record both connection
generations, and switch A -> B -> A. In each echo world, invoke
`/imp_capture_gmcp Core.Ping {}` once. Both established worlds must retain
their connection generations; the select and synthetic GMCP events must remain
valid, and neither operation may trigger a reset. This is the live-only check
that each dynamic generation lookup and all dependent commands execute in one
`/eval` scope.

Perform and record every check:

1. **Exact-current context, literal data, and one-shot replacement.** With
   `ImpA` selected, the result is `forwarded` and exactly one literal
   `/#7 say "100% ready"; /look` line appears in `ImpA`. Nothing appears in
   `ImpB`. Immediately type a visible command at the TinyFugue keyboard;
   input must remain responsive. Then send a second uniquely tagged literal and
   verify it also appears exactly once in A, proving the first helper exited and
   its guarded replacement registered. This traverses `RelayActionSink`,
   `/action`, the broker, `/action-consumer`, the Python helper, quote-pinned
   stdout, `/imp_send`, `textdecode()`, and `send()`. `forwarded` itself
   still proves only the bridge write.
   This is also the live-only check that the dynamic per-world connection
   lookup and the full fence execute in one `/eval` scope; deterministic tests
   enforce source shape but do not execute TinyFugue's scope semantics.
2. **SEND-hook bypass.** Before repeating the exact-current send, install a
   harmless temporary counter:

   ```text
   /set imp_accept_send_hooks=0
   /def -i -h"SEND *" imp_accept_send_hook = \
       /test imp_accept_send_hooks := imp_accept_send_hooks + 1
   ```

   The literal line must still appear exactly once and the counter must remain
   zero, proving the final `send()` neither invokes nor transforms through a
   `SEND` hook. Remove it afterward with
   `/undef imp_accept_send_hook`.

3. **Foreground race fence.** Save `contextA`, then freeze the running feed
   process without stopping its service or running feed shutdown cleanup. In a
   VPS shell, obtain and validate the user service's current main PID, suspend
   it, and install a cleanup trap before changing TinyFugue:

   ```sh
   feed_pid="$(systemctl --user show --property MainPID --value imp-feed.service)"
   test "${feed_pid:-0}" -gt 0 && kill -0 "$feed_pid"
   trap 'kill -CONT "$feed_pid" 2>/dev/null || true' EXIT INT TERM
   kill -STOP "$feed_pid"
   systemctl --user show --property ActiveState,SubState,MainPID imp-feed.service
   ps -o pid=,stat=,cmd= -p "$feed_pid"
   ```

   Do not proceed unless the nonzero-PID and `kill -0` validation succeeds.

   Confirm the service process still exists and is stopped by signal, while the
   relay active context, context marker, and registered A helper still represent
   `contextA`. Leave TinyFugue running and interactive. Run
   `/world ImpB`, but do not resume the feed yet. Immediately call
   `send(contextA, literal)` through the production sink. The result must be
   `forwarded`, proving the helper wrote and flushed the fixed bridge line, and
   neither echo world may show the literal line. That zero-echo result then
   proves the synchronous `/imp_send` fence rejected the command because
   TinyFugue's live foreground/world generation no longer matches; it must never
   retarget to B.

   Resume the same process and remove the trap:

   ```sh
   kill -CONT "$feed_pid" && trap - EXIT INT TERM
   ```

   Wait for normal spool processing to catch up, then verify the marker and
   relay converge on B and the feed resumes publishing.

4. **Active connection-generation fence.** Select A, save its exact current
   context/generation, and repeat the same `MainPID` validation, `SIGSTOP`, and
   cleanup-trap sequence above. Confirm the relay, marker, and registered helper
   still retain the old A generation. While the feed remains frozen, run
   `/imp_reset_world ImpA` to advance A's local connection generation
   using the connectionless mechanism. Before `SIGCONT`, call
   `send(contextA, literal)` with the old retained context. The result must be
   `forwarded`, proving the fixed bridge was reached, while no echo line may
   appear because `/imp_send` sees a different current local connection
   generation. Resume the feed, remove the trap, allow queued spool processing
   to catch up, and verify the marker and relay converge on A's new generation.

   For either fence probe, always send `SIGCONT` even if any assertion or command
   fails. Verify the feed processes new input afterward and never leave the
   service process suspended. Do not use `systemctl --user stop`/`start` for
   these fence proofs: orderly shutdown removes the marker and helper, so a
   rejected action would not exercise `/imp_send`.

5. **Quote pinning.** With an exact-current A context, submit a uniquely tagged
   action and immediately switch to B. Accepted work may appear once in its
   quote-pinned A world or be suppressed by the synchronous fence; it must never
   appear in B.
6. **Idle relay restart.** While no state event is arriving, restart
   `imp-relay.service`. The publisher must reconnect and reassert the
   retained selection, which remains `stale` until genuine state is published;
   the consumer must re-register. No earlier literal may reappear. A new unique
   action must then produce exactly one new A echo line.
7. **Helper loss.** Identify and terminate only the helper process whose argv
   carries this test session/context. An action during the loss is rejected or
   `unknown` and produces no line. Switch away and back to start a current
   helper, then send one new unique action. It appears exactly once; the lost
   action is never retried or duplicated.
8. **Idle shutdown.** With a current helper registered and no action in flight,
   run `/quit -y`. TinyFugue must exit promptly without waiting in `do_wait`.
   Confirm no TinyFugue process remains with an action-helper descendant. This
   proves closing the quote-pipe reader terminates the idle helper and its shell
   intermediary without requiring another action or relay failure.

Record the TinyFugue build, the two connectionless world definitions, before
and after context tuples, action results, hook counter, per-world echo output,
relay restart health, helper-loss result, `/quit -y` completion, and the
post-quit process check. Only that evidence can change the status above.
Automated tests and the structural macro contract are not TinyFugue execution.

#### Recorded acceptance evidence

The live run established the following:

- Two exact-current actions each returned `forwarded`, appeared exactly once
  in `ImpA`, left TinyFugue immediately interactive, and caused the
  one-shot helper to exit and be replaced for the same exact context. This also
  executed the corrected `imp_send` `/eval` scope.
- With the feed suspended on published A, a local A -> B transition advanced
  only the foreground generation. An action using the stale published A
  context returned `forwarded` but appeared in neither world; its stale helper
  exited without replacement.
- With the feed suspended on published B connection generation 102, a local
  `/imp_reset_world ImpB` advanced B to 122. An action carrying 102
  returned `forwarded` but appeared nowhere, and its stale helper exited
  without replacement. After feed resume, the marker converged to 122.
- In a fresh process, A -> B -> A changed only the foreground generation:
  A remained at connection 7, B at connection 8, and the serial remained 8.
  `/imp_capture_gmcp Core.Ping {}` in each echo world preserved those
  values, and the feed reported no malformed select or GMCP events. This
  executes the corrected `imp_select_world` and
  `imp_capture_gmcp` `/eval` scopes.
- An action accepted for current B followed by an immediate switch to A never
  appeared in A. The B-side outcome was not observed: the contract permits
  delivery only to quote-pinned B or synchronous suppression if the foreground
  transition wins. The helper subsequently converged to current A.
- An idle current A helper survived a relay restart. The relay returned with a
  stale retained snapshot and one producer; a new action appeared exactly once,
  no earlier action replayed, TinyFugue stayed interactive, and a replacement
  helper appeared.
- After the current consumer was killed, an action returned
  `rejected` with `no matching TinyFugue consumer` and did not execute. A
  legitimate B -> A transition created a new helper; a fresh action then
  appeared exactly once, and the rejected action never appeared later.
- `/quit -y` exited promptly with an idle helper. No TinyFugue process,
  consumer, or wrapper shell remained, live-verifying terminal reader-loss
  handling and closure of the prior `pclose()`/`do_wait()` hang.

Relay `forwarded` means the action was written and flushed to the fixed
TinyFugue bridge. The synchronous TinyFugue fences may still suppress that line,
and neither `forwarded` nor connectionless echo proves MUD-server execution.
`tfwrite: bad handle` messages observed in the connectionless worlds were a
probe-world artifact; they did not prevent direct or Imp send delivery.

### Real-MUD acceptance

The operator-controlled real-MUD acceptance was completed with the approved
harmless command `look` from the native Windows UI. The action traversed
`RelayActionSink`, the relay, the matching TinyFugue consumer, and the MUD path
and was independently observed to execute exactly once. The UI reported
`Forwarded to TinyFugue. Final MUD delivery is not confirmed.` That wording
remains the contract: `forwarded` alone proves only the bridge write and flush,
not MUD receipt or execution.

Removing the matching consumer caused a later action to be rejected without
execution. Restoring a matching consumer did not replay that action; one fresh
action then executed exactly once. No queue, retry, replay, reconnect resend, or
duplicate invocation was observed. Deterministic tests cover `unknown` result
semantics and its no-retry wording, and manual browser mock acceptance checked
that presentation; no live `unknown` outcome was deliberately manufactured.

## Verified TinyFugue boundary

The base hook contract was established against TinyFugue
5.1.6-4-ga15a165 with `+gmcp` and `+GMCP`. The current VPS build
5.2.2-3-g4f0ff34 additionally provides the `GMCP_LOGIN` hook the operator's
login scripts need (see
[identity bootstrap prerequisite](#identity-bootstrap-prerequisite)).
`GMCP`, `CONNECT`, and `WORLD` supply the package/world events used by the
versioned hook. `GMCP_LOGIN` remains required by the operator's login scripts
for GMCP negotiation and identity bootstrap, but Imp does not treat it as
a new connection-generation boundary. `fwrite(filename, data)` appends data and a newline
to a fixed filename; `time()` supplies epoch seconds with six fractional
digits.

All Imp hooks use explicit priority 2 and `-F`. TinyFugue runs the
highest-priority matching hook first and stops unless it falls through. The
priority runs ahead of operator priority-1 handlers such as `received-gmcp`;
`-F` lets those handlers still run. Two same-priority non-fall-through GMCP
hooks previously lost complete events intermittently. Re-installing
`imp_capture_gmcp` at priority 2 with `-F` made repeated live fights
acquire, update, and clear targets correctly. Do not drop `-F`; any priority
change needs live re-verification.

The prior real capture included valid HUD records and malformed inventory
events whose MUD payloads contained unescaped controls inside JSON strings.
The converter logged only bounded error codes and did not repair or execute the
input. That evidence covers the GMCP parsing boundary. The context/action
runtime has separate connectionless fence evidence and the real-MUD native UI
acceptance recorded above.

## Observed real-session schema

The redacted fixture establishes these mappings:

- `Char.Status.character_name` supplies identity.
- `Char.Status.health`, `health_max`, `mana`, `mana_max`, `movement`, and
  `movement_max` provide full or partial resource updates.
- `Char.Vitals.hp`, `maxhp`, `mp`, `maxmp`, `mv`, and `maxmv` provide complete
  resource snapshots. All observed resource values are decimal strings.
- `Char.Status.opponent_name` supplies the target label.
  `opponent_health` is a percentage string; every acquisition paired it with
  `opponent_health_max` equal to `"100"`. Later damage updates supplied only
  `opponent_health`.
- An empty `opponent_name` with zero health fields clears the target.
- `Char.Vitals.position` is tracked only to end combat. AVATAR does not always
  emit the clear record above: a later capture ended a fight with no explicit
  clear at all, so both mechanisms are required. A `"Fight"` -> non-`"Fight"`
  transition is treated as combat end and clears the current target. An
  isolated non-`"Fight"` record cannot clear a live-acquired target while no
  `"Fight"` state has been observed for it. A target restored from the
  checkpoint is the exception: it has no observed combat history, so the first
  non-`"Fight"` position retires it rather than letting it outlive a restart.
- Clearing is one-way. A target is established only by a valid
  `opponent_name`; an `opponent_health`-only record with no established target
  creates none. Imp prefers showing no target over attaching a health
  percentage to an unknown or stale name.

Every field required by the current HUD was present. The stream did not expose
absolute target hit points; only the percentage-scale opponent fields were
available. Room, group, and inventory packages remain deliberately unmapped,
so valid unknown records preserve the exact previous HUD state.
