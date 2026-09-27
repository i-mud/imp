# 0001 - The relay binds to loopback and remote access uses SSH

Status: accepted; amended by ADR 0010
Date: 2026-09-15

Amendment (2026-09-27): ADR 0010 adds authenticated Direct WSS as a second
supported remote desktop transport through a separate loopback gateway. This
record remains authoritative for the relay itself: it stays loopback-only,
unauthenticated, and valid for SSH transport. Statements below that describe
SSH as the sole remote-access method are historical to this decision and are
superseded only in that respect by ADR 0010.

## Context

The relay runs on a VPS that also runs TinyFugue. The desktop HUD runs on a
workstation elsewhere. Something has to carry normalized state between them.

The obvious shapes were: expose the relay on a public port with its own
authentication, or keep it private and reuse an existing authenticated channel.

## Decision

The relay binds `127.0.0.1` by default and is never exposed publicly. Remote
access is an SSH port-forward that the operator already has to trust:

```
ssh -N -L <local-port>:127.0.0.1:<relay-port> <user>@<vps>
```

The relay implements no per-user authentication of its own.

## Rationale

Adding a bespoke auth scheme to the relay would mean inventing credential
storage, rotation and transport security. The supported deployment instead
places every host-local process inside the trust boundary and uses the
operator's existing authenticated SSH channel for remote access.

Loopback prevents remote network access, but TCP loopback does not enforce UID
or same-user ownership. Any process in the VPS network namespace can reach the
relay, including one owned by another local OS user. The workstation side of an
SSH forward has the same host-local property. Browser `Origin` checks are
defense-in-depth against cross-site requests, not authentication.

TinyScry therefore supports a single-user workstation and VPS, or hosts where
all local users and processes are mutually trusted. An untrusted multi-user
host is outside the supported trust boundary.

## Consequences

- A non-loopback bind is rejected. There is no override flag.
- TinyScry has no per-user authentication on the VPS listener or workstation
  forward; loopback must never be described as same-user isolation.
- The relay must never grow authentication as a way to make public exposure
  acceptable. If public exposure is ever genuinely needed, that is a new
  decision record, not a patch to this one.
- The HUD always connects to `localhost`; it cannot tell a tunnel from a local
  relay, which is what makes automated tunnel management a later, additive
  change (see 0003 and `docs/architecture/objects/state-source.md`).
- The first slice ships the tunnel as a documented manual command. Automating
  it would require handling private keys and host verification, which is real
  security work and must not be rushed into a bootstrap.
