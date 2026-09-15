; TinyScry GMCP record emitter for TinyFugue.
; Run TinyFugue with stdout connected directly to tinyscry-bridge. This file
; intentionally has no shell invocation and never evaluates GMCP as TF code.

; UNVERIFIED: confirm this build's direct telnet-write escape handling with
; `tf --help send` before enabling GMCP option 201. This request is static;
; no server-supplied value participates in it.
/def tinyscry_enable_gmcp = /send -w%% \
  \377\372\311Core.Supports.Set ["Char 1","IRE.Target 1"]\377\360

; The hook below is deliberately separate from formatting. `%1` must be the
; GMCP package token and `%2` must be the original JSON payload, not TF-quoted
; or command-expanded text. A GMCP package token cannot contain a JSON quote;
; the payload is embedded as JSON, never as a JSON string.
;
; UNVERIFIED: verify the GMCP callback/trigger name and positional argument
; shape in this VPS's TF manual. Vanilla TinyFugue builds differ here. Bind the
; verified callback to `tinyscry_gmcp_record`; do not copy raw GMCP into /sh,
; /quote, command substitution, or a filename.
/def tinyscry_gmcp_record = /echo {"at":%{time()},"package":"%1","payload":%2}

; UNVERIFIED: verify that %{time()} returns a non-negative epoch-millisecond
; integer. If it returns seconds or formatted text, the build-specific hook
; must produce milliseconds before calling tinyscry_gmcp_record.
;
; UNVERIFIED: verify the actual mechanism for routing /echo output alone to
; the bridge stdin or FIFO. Use a direct descriptor/write API, never a shell.
; TF's JSON escaping is not verified in this project; therefore only bind this
; macro when the callback guarantees a package token and an untouched JSON
; payload. Otherwise use the safe capture/replay path documented in README.md.
