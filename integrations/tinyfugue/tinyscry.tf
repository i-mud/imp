; TinyScry GMCP capture hook for TinyFugue 5.1.6-4-ga15a165.
;
; Before loading this file, create ~/.local/state/tinyscry with mode 0700 and
; pre-create gmcp.raw with mode 0600. The fixed filename never contains MUD
; data. fwrite() appends directly; no shell or TF command evaluation occurs.
;
; This build passes a GMCP hook one raw argument in the form "Package JSON".
; {*} preserves that argument as data. time() returns epoch seconds with six
; fractional digits; tinyscry-capture performs the checked millisecond
; conversion and JSON envelope encoding.
/def -ag -h"GMCP" tinyscry_capture_gmcp = \
    /test fwrite("~/.local/state/tinyscry/gmcp.raw", strcat(time(), " ", {*}))
