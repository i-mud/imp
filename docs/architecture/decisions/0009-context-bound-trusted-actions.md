# 0009 - Outbound actions are context-bound and forwarded through a fixed TinyFugue macro

Status: accepted
Date: 2026-09-19

Scope note: this records the original TinyFugue action integration. The exact
context and no-retry/no-replay rules remain node-wide; the fixed macro and
write/flush meaning of `forwarded` apply specifically to TinyFugue. Mudlet uses
its own context-fenced command API, not a TinyFugue macro. See
[`0011-local-client-adapters-and-imp-node.md`](0011-local-client-adapters-and-imp-node.md)
and [`../objects/desktop-actions.md`](../objects/desktop-actions.md).

## Context

Imp needs a narrow path for future desktop controls to send an operator
command to the foreground MUD connection. The existing pipeline was deliberately
state-only. Adding a generic remote-execution path, passing MUD data through a
shell, or guessing which TinyFugue world should receive a command would violate
that boundary.

TinyFugue can have several worlds connected at once. A world name alone is not
enough: the same world may reconnect while an old helper or desktop snapshot is
still alive. Delivery can also become ambiguous if a helper disconnects after a
write but before its acknowledgement reaches the relay.

## Decision

Every selected state and action carries an exact context tuple:

```text
(session, foreground generation, connection generation)
```

The relay retains only the selected context. It forwards an action only when the
request, retained state, and one registered TinyFugue helper all have the same
tuple. There is one action in flight globally and no queue, retry, broadcast, or
replay. A timeout or disconnect after dispatch returns `unknown`; it never causes
an automatic resend.

The helper receives action text as protocol data, checks a private context marker
immediately before delivery, text-encodes the command, and writes one fixed
`/imp_send <session> <foreground> <connection> <world-token> <encoded-data>`
line to TinyFugue. Each registration accepts at most one dispatch, and the
helper exits after writing and flushing that line so TinyFugue never waits for
more output from an open shell-quote pipe. While idle, it writes no liveness
bytes; it watches its stdout descriptor for terminal reader loss and exits
without a result or reconnect when TinyFugue closes the quote pipe. At execution
the macro rechecks all four locally generated values against the current TF
session, foreground generation, selected and quote-pinned world, and world
connection generation before calling `send()` and starting a replacement helper
inside the same guard. A stale line therefore neither sends nor recreates its
obsolete context. The raw command is never shell argv, shell syntax, a generated
TF command name, or evaluated TF source.

The desktop exposes this path through an `ActionSink`, separate from
`StateSource`. Mock mode uses an in-memory sink and performs no network access.

The relay remains permanently loopback-only. Loopback prevents remote network
access but does not enforce UID or same-user ownership: another local user or
process in the same host/network namespace can reach the listener on either the
VPS or workstation. Browser-facing endpoints allow only no `Origin`, the
development origin, or the Tauri origin, while producer/helper endpoints reject
every browser `Origin`. These checks are browser defense-in-depth, not
authentication. Imp provides no per-user endpoint authentication and
supports only single-user hosts or hosts whose local users/processes are all
mutually trusted; untrusted multi-user hosts are out of scope.

## Consequences

- Switching worlds or reconnecting changes context and makes every old desktop
  snapshot and helper ineligible immediately.
- `forwarded` means only that the helper successfully wrote and flushed the
  fixed line into TinyFugue's fixed action bridge. It does not prove that the
  synchronous local fence passed, `send()` succeeded, the MUD socket received
  the command, or the MUD executed it.
- A caller may present `unknown` to the operator but must not retry it
  automatically; duplicate MUD commands are worse than an explicit ambiguity.
- Only printable ASCII commands of 1..512 characters are accepted. This keeps
  the current bridge narrow; expanding the command language requires a protocol
  decision and new boundary fixtures.
- The action path does not provide arbitrary TinyFugue scripting or shell
  execution. Those are explicitly out of scope.
