; TinyScry GMCP capture hook for TinyFugue 5.1.6-4-ga15a165.
;
; `tinyscry-feed` owns ~/.local/state/tinyscry/spool and keeps it a symlink
; into its private runtime directory, replaced on every feed (re)start; this
; file itself never needs to change. Verified: /def with a fixed macro name
; replaces rather than duplicates a prior definition, so loading this file
; more than once - a second /load, or a .tfrc sourced twice - is safe and
; leaves exactly one hook registered.
;
; fwrite() reopens and closes the target on every call (fopen "a" + fclose),
; so it always follows the current symlink target; no TinyFugue restart is
; needed after the feed restarts. If the feed is down, has not started yet,
; or the target directory is missing, fwrite() reports one error line and
; continues - measured: TinyFugue never blocks on this write, unlike a FIFO
; with no reader (measured 5.97s hang) or a slow reader (measured indefinite
; hang once the pipe filled). Losing hook lines while the feed is
; unavailable is expected; TinyFugue must never be.
;
; This build passes a GMCP hook one raw argument in the form "Package JSON".
; {*} preserves that argument as data. time() returns epoch seconds with six
; fractional digits; tinyscry-feed performs the checked millisecond
; conversion and JSON envelope encoding.
;
; -F (fall-through) and an explicit priority 2 keep this hook observational.
; TinyFugue runs the highest-priority matching hook first and stops there
; unless that hook falls through. Operators commonly already own a generic
; GMCP hook at the default priority 1, such as `received-gmcp`; two
; same-priority non-fall-through hooks on one event compete, and in live
; testing that intermittently lost whole GMCP events - some fights never
; acquired a target while resource updates kept arriving. TinyScry's priority
; 2 runs ahead of the operator's priority-1 handler, and -F lets the lower-
; priority handler still run afterward. This means TinyScry observes every
; inbound GMCP event without consuming it. Do not drop -F. Priority 2 is the
; shipped, live-verified configuration; any priority change should be
; re-verified against operator GMCP handlers.
/def -Fp2 -ag -h"GMCP" tinyscry_capture_gmcp = \
    /test fwrite("~/.local/state/tinyscry/spool", strcat(time(), " ", {*}))
