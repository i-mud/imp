; Imp context-aware capture and action bridge for TinyFugue 5.2.2-3-g4f0ff34.
;
; The spool path and helper command are fixed operator configuration. GMCP data
; is written only as data to the private spool; it never reaches a shell argv.
; Action text returns from the helper as a textencode.tf token and is decoded
; only inside the fixed /imp_send macro. send() deliberately bypasses SEND
; hooks. /quote is asynchronous, uses -dexec, and blank -w pins the world that
; was current when the helper started.

/require textencode.tf

/if (!isvar("imp_session")) \
    /test imp_session := textencode(strcat(getpid(), ".", time()))%; \
/endif
/if (!isvar("imp_connection_serial")) /set imp_connection_serial=0%; /endif
/if (!isvar("imp_foreground")) /set imp_foreground=0%; /endif
/if (!isvar("imp_selected_world")) /set imp_selected_world=%; /endif

; /eval creates a nested local scope. Any /let inside /eval and every dependent
; command must remain in that same expansion.
/def -i imp_send = \
    /let _expected_session=%{1}%; \
    /let _expected_foreground=%{2}%; \
    /let _expected_connection=%{3}%; \
    /let _expected_world=%{4}%; \
    /let _pinned_world=$[textencode(world_info())]%; \
    /eval \
        /if (_expected_session =~ imp_session & \
            _expected_foreground = imp_foreground & \
            _expected_connection =~ %%{imp_connection_%{_pinned_world}} & \
            _expected_world =~ imp_selected_world & \
            _expected_world =~ _pinned_world) \
            /test send(textdecode({5}))%%; \
            /imp_start_consumer %{_expected_connection} %{_expected_world}%%; \
        /endif

/def -i imp_start_consumer = \
    /quote -0 -dexec -w !~/.local/bin/imp-action-consumer --session %{imp_session} \
        --foreground %{imp_foreground} --connection %{1} --world %{2} 2>/dev/null

/def -i imp_reset_world = \
    /let _world=$[textencode({1})]%; \
    /test imp_connection_serial := imp_connection_serial + 1%; \
    /eval /set imp_connection_%{_world}=%{imp_connection_serial}%; \
    /test fwrite("~/.local/state/imp/spool", \
        strcat("IMP2 R ", imp_session, " ", imp_connection_serial, " ", _world, " ", time()))%; \
    /if (_world =~ imp_selected_world) \
        /test fwrite("~/.local/state/imp/spool", \
            strcat("IMP2 S ", imp_session, " ", imp_foreground, " ", \
                imp_connection_serial, " ", _world, " ", time()))%; \
        /imp_start_consumer %{imp_connection_serial} %{_world}%; \
    /endif

/def -i imp_select_world = \
    /let _world=$[textencode({1})]%; \
    /if (_world !~ imp_selected_world) \
        /test imp_foreground := imp_foreground + 1%; \
        /set imp_selected_world=%{_world}%; \
    /endif%; \
    /if (strlen(_world) = 0) \
        /test fwrite("~/.local/state/imp/spool", \
            strcat("IMP2 S ", imp_session, " ", imp_foreground, " 0 - ", time()))%; \
    /else \
        /eval \
            /let _connection=%%{imp_connection_%{_world}}%%; \
            /if (!strlen(_connection) | !is_connected(textdecode(_world))) \
                /test fwrite("~/.local/state/imp/spool", \
                    strcat("IMP2 S ", imp_session, " ", imp_foreground, " 0 - ", time()))%%; \
            /else \
                /test fwrite("~/.local/state/imp/spool", \
                    strcat("IMP2 S ", imp_session, " ", imp_foreground, " ", \
                        _connection, " ", _world, " ", time()))%%; \
                /imp_start_consumer %%{_connection} %{_world}%%; \
            /endif%; \
    /endif

/def -Fp2 -ag -h"CONNECT" imp_capture_connect = /imp_reset_world %{1}
/def -Fp2 -ag -h"WORLD" imp_capture_world = /imp_select_world %{1}

/def -Fpmaxpri -q -mregexp -t"(.*)" imp_capture_text = \
    /let _world_name=$[world_info()]%; \
    /let _world=$[textencode(_world_name)]%; \
    /if (_world =~ imp_selected_world & strlen({*}) > 0 & strlen({*}) <= 1024) \
        /eval \
            /let _connection=%%{imp_connection_%{_world}}%%; \
            /if (strlen(_connection)) \
                /test fwrite("~/.local/state/imp/spool", \
                    strcat("IMP2 T ", imp_session, " ", _connection, " ", \
                        _world, " ", time(), " ", textencode({*})))%%; \
            /endif%; \
    /endif

/def -Fp2 -ag -h"GMCP" imp_capture_gmcp = \
    /let _world_name=$[world_info()]%; \
    /let _world=$[textencode(_world_name)]%; \
    /eval \
        /let _connection=%%{imp_connection_%{_world}}%%; \
        /if (!strlen(_connection)) \
            /imp_reset_world %{_world_name}%%; \
            /let _connection=%%{imp_connection_%{_world}}%%; \
        /endif%%; \
        /test fwrite("~/.local/state/imp/spool", \
            strcat("IMP2 G ", imp_session, " ", _connection, " ", _world, " ", time(), " ", {*}))
