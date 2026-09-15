# 0001 - The relay binds to loopback and SSH is the only security boundary

Status: accepted
Date: 2026-09-15

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

The relay implements no authentication of its own.

## Rationale

Adding a bespoke auth scheme to the relay would mean inventing credential
storage, rotation and transport security for a service whose entire audience is
one operator who already holds SSH access to the same host. That is more attack
surface and more code defending a boundary SSH already defends properly.

Loopback-only also means the relay's threat model is "a local process on the
VPS", not "the internet", which is what justifies the relay having no
authentication at all.

## Consequences

- A non-loopback bind must be an explicit, logged opt-in - see
  `services/relay/src/tinyscry_relay/config.py`.
- The relay must never grow authentication as a way to make public exposure
  acceptable. If public exposure is ever genuinely needed, that is a new
  decision record, not a patch to this one.
- The HUD always connects to `localhost`; it cannot tell a tunnel from a local
  relay, which is what makes automated tunnel management a later, additive
  change (see 0003 and `docs/architecture/objects/state-source.md`).
- The first slice ships the tunnel as a documented manual command. Automating
  it would require handling private keys and host verification, which is real
  security work and must not be rushed into a bootstrap.
