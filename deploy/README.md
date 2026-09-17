# VPS deployment

Runs `services/relay` and `integrations/tinyfugue`'s live feed as `systemd --user`
services, so the pipeline survives a VPS reboot and an administrator's SSH
session ending, without a root-owned service and without exposing anything
beyond loopback. Everything below runs **on the VPS** unless labelled
otherwise.

## 1. Sync the repository (in WSL/repo)

The `tinyscry-tinyfugue` package depends on `tinyscry-relay` by relative path
(see its `pyproject.toml` `[tool.uv.sources]`), so sync the whole repository
rather than individual subdirectories:

```bash
rsync -a --delete \
  --exclude .git --exclude node_modules --exclude dist --exclude target \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .mypy_cache --exclude .ruff_cache \
  ~/src/tinyscry/ <host-alias>:~/tinyscry/
```

Replace `<host-alias>` with the VPS's entry in `~/.ssh/config`. Re-run this
after every change that needs to reach the VPS; the WSL2 checkout stays
authoritative.

## 2. Install the Python environments (on VPS)

```bash
cd ~/tinyscry
uv sync --project services/relay
uv sync --project integrations/tinyfugue
```

This is the same `uv sync` step documented in the root
[`README.md`](../README.md); it creates `.venv/` under each project, which is
where the unit files point.

## 3. Install the systemd user units (on VPS)

```bash
mkdir -p ~/.config/systemd/user
cp ~/tinyscry/deploy/systemd/tinyscry-relay.service ~/.config/systemd/user/
cp ~/tinyscry/deploy/systemd/tinyscry-feed.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

## 4. Enable lingering (on VPS, once)

Without lingering, `systemd --user` (and everything it manages) stops the
moment your last session logs out, and `$XDG_RUNTIME_DIR` may not exist at
boot for a user with no active login:

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger   # expect: Linger=yes
```

## 5. Enable and start the services (on VPS)

```bash
systemctl --user enable --now tinyscry-relay.service tinyscry-feed.service
systemctl --user status tinyscry-relay.service tinyscry-feed.service
```

`tinyscry-feed.service` `Wants=` (not `Requires=`) the relay: if the relay is
briefly down, the feed keeps running and reconnects with the publisher's
existing bounded backoff rather than failing.

## 6. Install the TinyFugue hook (on VPS)

Copy the unchanged hook to TinyScry's config directory:

```bash
mkdir -p ~/.config/tinyscry
cp ~/tinyscry/integrations/tinyfugue/tinyscry.tf ~/.config/tinyscry/capture.tf
```

Then add this line to the startup file used by the operator's actual
TinyFugue invocation:

```text
/load ~/.config/tinyscry/capture.tf
```

Do not have the installer create or overwrite an operator startup file.
TinyFugue's `-f FILE` option loads `FILE` instead of the normal personal
config, so `~/.tfrc` is not necessarily active. For example, when starting
from `~/avatar/tf` with `tf -f./.tfrc -n`, add the line to
`~/avatar/tf/.tfrc`.

Load the hook before anything in that startup path can connect or log in to
the MUD. AVATAR sends the full identity-bearing `Char.Status` during login;
loading the hook afterward can leave TinyScry without an identity until the
next login. Loading is additive: TinyScry does not own or replace the
operator's GMCP negotiation or connection macros. Repeated loads are safe -
`/def` replaces the named capture macro rather than duplicating it.

The hook writes to the fixed path `~/.local/state/tinyscry/spool`.
`tinyscry-feed` owns that path as a symlink into its private runtime
directory and replaces it on every (re)start; nothing about the hook file
changes when the feed restarts. See
[`integrations/tinyfugue/README.md`](../integrations/tinyfugue/README.md) for
why this is a plain drained file rather than a FIFO.

## Verifying the deployment

```bash
# Relay listens on loopback only
ss -ltnp | grep 8787
# expect exactly: 127.0.0.1:8787, no 0.0.0.0 or :: entry

# Both services are active
systemctl --user is-active tinyscry-relay.service tinyscry-feed.service

# The feed created the runtime spool and the hook symlink
ls -l ~/.local/state/tinyscry/spool   # -> symlink into $XDG_RUNTIME_DIR/tinyscry/spool

# Recent lifecycle logs, never raw GMCP
journalctl --user -u tinyscry-relay.service -u tinyscry-feed.service -n 50
```

## Common failure diagnostics

| Symptom                                                                  | Check                                                                                                                                                                                                              |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tinyscry-feed` exits immediately with "another TinyScry feed holds ..." | A duplicate instance is running - manual invocation while the service is active, or a second service instance. `systemctl --user status tinyscry-feed.service`, then stop the extra process.                       |
| Relay reachable but no HUD data                                          | `curl http://127.0.0.1:8787/healthz` (via the SSH tunnel) and confirm a producer is attached; check `journalctl --user -u tinyscry-feed.service` for "feed started" / reconnect lines.                             |
| TinyFugue shows an `fwrite` error line                                   | The feed is down or the hook symlink target directory is missing. TinyFugue is not blocked by this - it is the intended fail-open behaviour - but no HUD update reaches the relay until the feed is running again. |
| Services do not survive a reboot                                         | Confirm `loginctl show-user "$USER" -p Linger` reports `Linger=yes`; without it, user units never start without an interactive login.                                                                              |
| Relay bound to more than loopback                                        | Never pass `--allow-non-loopback` in the unit file; re-run step 3 from a clean copy of `deploy/systemd/tinyscry-relay.service`.                                                                                    |

## Diagnostic raw capture (opt-in, VPS)

Normal operation keeps raw GMCP only in the bounded private runtime spool
under `$XDG_RUNTIME_DIR`; it retains no raw diagnostic history. To capture a
bounded, private diagnostic trail temporarily:

```bash
systemctl --user edit tinyscry-feed.service
```

Add an override:

```ini
[Service]
ExecStart=
ExecStart=%h/tinyscry/integrations/tinyfugue/.venv/bin/tinyscry-feed --diagnostic-capture
```

Then `systemctl --user daemon-reload && systemctl --user restart
tinyscry-feed.service`. Files land privately under
`~/.local/state/tinyscry/diagnostics/`, rotate at 1 MiB, and keep at most 5
files. Remove the override (`systemctl --user revert tinyscry-feed.service`)
when done; this is a debugging aid, not a default.
