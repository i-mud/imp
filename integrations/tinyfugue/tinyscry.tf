; TinyScry context-aware capture and action bridge for TinyFugue 5.2.2-3-g4f0ff34.
;
; The spool path and helper command are fixed operator configuration. GMCP data
; is written only as data to the private spool; it never reaches a shell argv.
; Action text returns from the helper as a textencode.tf token and is decoded
; only inside the fixed /tinyscry_send macro. send() deliberately bypasses SEND
; hooks. /quote is asynchronous, uses -dexec, and blank -w pins the world that
; was current when the helper started.

/require textencode.tf

/if (!isvar("tinyscry_session")) \
    /test tinyscry_session := textencode(strcat(getpid(), ".", time()))%; \
/endif
/if (!isvar("tinyscry_connection_serial")) /set tinyscry_connection_serial=0%; /endif
/if (!isvar("tinyscry_foreground")) /set tinyscry_foreground=0%; /endif
/if (!isvar("tinyscry_selected_world")) /set tinyscry_selected_world=%; /endif

; /eval creates a nested local scope. Any /let inside /eval and every dependent
; command must remain in that same expansion.
/def -i tinyscry_send = \
    /let _expected_session=%{1}%; \
    /let _expected_foreground=%{2}%; \
    /let _expected_connection=%{3}%; \
    /let _expected_world=%{4}%; \
    /let _pinned_world=$[textencode(world_info())]%; \
    /eval \
        /if (_expected_session =~ tinyscry_session & \
            _expected_foreground = tinyscry_foreground & \
            _expected_connection =~ %%{tinyscry_connection_%{_pinned_world}} & \
            _expected_world =~ tinyscry_selected_world & \
            _expected_world =~ _pinned_world) \
            /test send(textdecode({5}))%%; \
            /tinyscry_start_consumer %{_expected_connection} %{_expected_world}%%; \
        /endif

/def -i tinyscry_start_consumer = \
    /quote -0 -dexec -w !~/.local/bin/tinyscry-action-consumer --session %{tinyscry_session} \
        --foreground %{tinyscry_foreground} --connection %{1} --world %{2} 2>/dev/null

/def -i tinyscry_reset_world = \
    /let _world=$[textencode({1})]%; \
    /test tinyscry_connection_serial := tinyscry_connection_serial + 1%; \
    /eval /set tinyscry_connection_%{_world}=%{tinyscry_connection_serial}%; \
    /test fwrite("~/.local/state/tinyscry/spool", \
        strcat("TS2 R ", tinyscry_session, " ", tinyscry_connection_serial, " ", _world, " ", time()))%; \
    /if (_world =~ tinyscry_selected_world) \
        /test fwrite("~/.local/state/tinyscry/spool", \
            strcat("TS2 S ", tinyscry_session, " ", tinyscry_foreground, " ", \
                tinyscry_connection_serial, " ", _world, " ", time()))%; \
        /tinyscry_start_consumer %{tinyscry_connection_serial} %{_world}%; \
    /endif

/def -i tinyscry_select_world = \
    /let _world=$[textencode({1})]%; \
    /if (_world !~ tinyscry_selected_world) \
        /test tinyscry_foreground := tinyscry_foreground + 1%; \
        /set tinyscry_selected_world=%{_world}%; \
    /endif%; \
    /if (strlen(_world) = 0) \
        /test fwrite("~/.local/state/tinyscry/spool", \
            strcat("TS2 S ", tinyscry_session, " ", tinyscry_foreground, " 0 - ", time()))%; \
    /else \
        /eval \
            /let _connection=%%{tinyscry_connection_%{_world}}%%; \
            /if (!strlen(_connection)) \
                /tinyscry_reset_world %{1}%%; \
                /let _connection=%%{tinyscry_connection_%{_world}}%%; \
            /endif%%; \
            /test fwrite("~/.local/state/tinyscry/spool", \
                strcat("TS2 S ", tinyscry_session, " ", tinyscry_foreground, " ", \
                    _connection, " ", _world, " ", time()))%%; \
            /tinyscry_start_consumer %%{_connection} %{_world}%; \
    /endif

/def -Fp2 -ag -h"CONNECT" tinyscry_capture_connect = /tinyscry_reset_world %{1}
/def -Fp2 -ag -h"GMCP_LOGIN" tinyscry_capture_gmcp_login = /tinyscry_reset_world %{1}
/def -Fp2 -ag -h"WORLD" tinyscry_capture_world = /tinyscry_select_world %{1}

/def -Fp2 -ag -h"GMCP" tinyscry_capture_gmcp = \
    /let _world_name=$[world_info()]%; \
    /let _world=$[textencode(_world_name)]%; \
    /eval \
        /let _connection=%%{tinyscry_connection_%{_world}}%%; \
        /if (!strlen(_connection)) \
            /tinyscry_reset_world %{_world_name}%%; \
            /let _connection=%%{tinyscry_connection_%{_world}}%%; \
        /endif%%; \
        /test fwrite("~/.local/state/tinyscry/spool", \
            strcat("TS2 G ", tinyscry_session, " ", _connection, " ", _world, " ", time(), " ", {*}))
