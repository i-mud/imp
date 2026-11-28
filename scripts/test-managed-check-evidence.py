#!/usr/bin/env python3
"""Deterministic checks for the Managed SSH fixture's evidence boundary."""

import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext, redirect_stdout
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

FIXTURE = runpy.run_path(str(Path(__file__).with_name("test-managed-check.py")))
classify = FIXTURE["classify_evidence"]
version = FIXTURE["ssh_version"]
read_evidence = FIXTURE["read_startup_evidence"]


class DiagnosticEvidenceTest(unittest.TestCase):
    OBSERVATION = (
        "ssh_observation stdout_eof=1 stdout_block=0 stdout_error=0 stderr_eof=1 stderr_block=0"
        " stderr_error=0 stderr_overflow=0 app_bytes=0 exit=-2147483648 fatal_called=1 host_site=0"
        " auth_site=0 stdio_site=1 preamble=0 final=stdio_fatal prefix=missing"
        " retirement=after_collection interference=none"
    )
    SESSION = (
        b"Connection from 127.0.0.1 port 50325 on 127.0.0.1 port 59900\r\n"
        b"Accepted publickey for private-runner from 127.0.0.1 port 50325 ssh2: ED25519 SHA256:private-key\r\n"
    )
    REQUEST = (
        b"debug1: server_input_channel_open: ctype direct-tcpip rchan 0 win 2097152 max 32768\r\n"
        b"debug1: server_request_direct_tcpip: originator 0.0.0.0 port 65535, target 127.0.0.1 port 8787\r\n"
    )
    CHANNEL = b"debug1: channel 2: new direct-tcpip [direct-tcpip] (inactive timeout: 0)\r\n"

    def test_client_only_accepts_one_complete_allowlisted_record(self):
        observation = FIXTURE["client_observation"]
        line = self.OBSERVATION
        self.assertEqual(observation(["pid 5", line, "result connection_failed"]), line)
        for invalid in (
            [line, line], [line + " PRIVATE"], [line.replace("exit=-2147483648", "exit=-2147483649")],
            [line.replace("exit=-2147483648", "exit=2147483648")], [line.replace("exit=-2147483648", "exit=-0")],
            [line.replace("stdio_fatal", "stdio_success")], [line.replace("stderr_error=0", "stderr_error=2")],
            [line.replace("prefix=missing ", "")], [line.replace(" prefix=", "\tprefix=")],
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(observation(invalid))

    def test_server_separates_tcp_completion_from_ssh_channel_messages(self):
        classify_server = FIXTURE["managed_server_evidence"]
        cases = (
            (b"refused local port forward: originator 0.0.0.0 port 65535, target 127.0.0.1 port 8787\r\n"
             b"debug3: send packet: type 92\r\n", "observed", "refused", "unobserved", "unobserved"),
            (b"Received request from 127.0.0.1 port 50325 to connect to host 127.0.0.1 port 8787,"
             b" but the request was denied.\r\n", "observed", "unobserved", "unobserved", "unobserved"),
            (self.CHANNEL + b"debug1: channel 2: connection failed: Connection refused\r\n"
             b"error: connect_to 127.0.0.1 port 8787: failed.\r\n"
             b"debug3: send packet: type 92\r\n", "unobserved", "refused", "unobserved", "failed"),
            (b"error: connect to 127.0.0.1 port 8787 failed: PRIVATE\r\n",
             "unobserved", "unobserved", "unobserved", "failed"),
            (self.CHANNEL + b"debug1: server_input_channel_open: confirm direct-tcpip\r\n",
             "unobserved", "unobserved", "unobserved", "unobserved"),
            (self.CHANNEL + b"debug1: channel 2: connected to 127.0.0.1 port 8787\r\n",
             "unobserved", "unobserved", "unobserved", "connected"),
            (self.CHANNEL + b"debug1: channel 2: connected to 127.0.0.1 port 8787\r\n"
             b"debug3: send packet: type 91\r\ndebug2: channel 2: rcvd close\r\n",
             "unobserved", "accepted", "observed", "connected"),
            (self.CHANNEL + b"debug3: send packet: type 91\r\n"
             b"debug2: channel 3: rcvd close\r\ndebug2: channel 2: send close\r\n",
             "unobserved", "accepted", "unobserved", "unobserved"),
            (self.CHANNEL + b"debug3: send packet: type 91\r\n"
             b"debug2: channel 2: send close for remote id 0\r\ndebug3: send packet: type 97\r\n",
             "unobserved", "accepted", "observed", "unobserved"),
            (self.CHANNEL + b"debug2: channel 2: rcvd close\r\n"
             b"debug3: send packet: type 91\r\ndebug3: send packet: type 97\r\n",
             "unobserved", "accepted", "unobserved", "unobserved"),
        )
        for data, denied, opened, closed, tcp in cases:
            with self.subTest(data=data):
                self.assertEqual(
                    classify_server(self.SESSION + self.REQUEST + data, "private-runner"),
                    f"log=sampled scope=session publickey=observed direct_tcpip=observed policy_denial={denied} "
                    f"ssh_open={opened} ssh_close={closed} target_tcp={tcp}",
                )

    def test_server_only_attributes_unique_matching_sessions(self):
        classify_server = FIXTURE["managed_server_evidence"]
        window = "log=sampled scope=case-window publickey="
        target = (" direct_tcpip=observed policy_denial=unobserved ssh_open=unobserved"
                  " ssh_close=unobserved target_tcp=unobserved")
        for data, expected in (
            (self.REQUEST, window + "unobserved" + target),
            (self.SESSION + self.SESSION + self.REQUEST, "log=ambiguous"),
            (self.SESSION.replace(b"50325 on", b"12345 on") + self.REQUEST, window + "observed" + target),
            (self.SESSION.replace(b"for private-runner", b"for other") + self.REQUEST, window + "unobserved" + target),
            (self.SESSION + self.REQUEST + self.REQUEST, "log=ambiguous"),
            (self.SESSION + self.REQUEST.replace(b"port 8787", b"port 8788"),
             window + "observed direct_tcpip=unobserved"),
            (self.SESSION + self.REQUEST[:-2], window + "observed direct_tcpip=unobserved"),
            (self.SESSION + self.REQUEST.split(b"\r\n", 1)[1] + b"debug3: send packet: type 92\r\n",
             window + "observed" + target.replace("ssh_open=unobserved", "ssh_open=refused")),
        ):
            with self.subTest(data=data):
                self.assertEqual(classify_server(data, "private-runner"), expected)

    def test_server_window_is_bounded_and_listener_inventory_is_passive(self):
        observer = FIXTURE["observe_managed_server"]
        for label, payload, expected in (
            ("relay-absent", self.REQUEST, "log=sampled scope=case-window publickey=unobserved "
             "direct_tcpip=observed policy_denial=unobserved ssh_open=unobserved ssh_close=unobserved "
             "target_tcp=unobserved relay_listener_before=absent relay_listener_after=present"),
            ("forwarding-denied", b"x" * (FIXTURE["MANAGED_LOG_LIMIT"] + 1), "log=limit-exceeded"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "sshd.log"
                path.write_bytes(self.SESSION)
                out = io.StringIO()
                inventory = Mock(side_effect=[False, True])
                with patch.dict(observer.__wrapped__.__globals__, {"listening": inventory}):
                    with path.open("rb") as reader, redirect_stdout(out), observer(reader, label, "private-runner"):
                        with path.open("ab") as writer:
                            writer.write(payload)
                self.assertEqual(out.getvalue(), f"{sys.platform} {label}: sshd_observation {expected}\n")
                self.assertEqual([call.args for call in inventory.call_args_list],
                                 [(8787,), (8787,)] if label == "relay-absent" else [])


class StartupEvidenceTest(unittest.TestCase):
    def test_client_observations_are_distinct(self):
        cases = (
            (b"Host key verification failed.\r\n", "host-verification"),
            (b"runner@127.0.0.1: Permission denied (publickey).\r\n", "authentication-rejected"),
            (b'Load key "C:/private/client_key": bad permissions\r\n', "key-permissions"),
            (b"bind [127.0.0.1]:50325: Address already in use\r\n", "bind-failed"),
            (b"channel 2: open failed: administratively prohibited: open failed\n", "forwarding-denied"),
            (b"channel 2: open failed: connect failed: Connection refused\n", "remote-connect-failed"),
        )
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify(data, "client"), [expected])

    def test_server_account_context_is_not_a_key_acl_failure(self):
        cases = (
            (b"Invalid user runner from 127.0.0.1 port 50325\r\n", "account-context"),
            (b"error: unable to get security token for user runner\n", "account-context"),
            (b"debug1: get_user_token - unable to generate user token for runner as i am not running as system\n",
             "account-context"),
            (b"error: CreateProcessAsUserW failed error:1314\n", "required-privilege"),
            (b"error: get_user_token - unable to generate sshd virtual token, ensure sshd service has TCB privileges\n",
             "required-privilege"),
            (b"Failed publickey for runner from 127.0.0.1 port 50325 ssh2: ED25519 SHA256:private\n",
             "authentication-rejected"),
            (b"debug3: Bad permissions. Try removing permissions for user: HOST\\runner on file private. [preauth]\n",
             "key-permissions"),
            (b"error: Bind to port 50325 on 127.0.0.1 failed: Address already in use.\n", "bind-failed"),
            (b"refused local port forward: originator 127.0.0.1 port 50325, target 127.0.0.1 port 8787\n",
             "forwarding-denied"),
        )
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify(data, "server"), [expected])

    def test_observe_preserves_terminal_evidence_and_discards_partial_lines(self):
        limit = FIXTURE["EVIDENCE_LIMIT"]
        startup = FIXTURE["WindowsStartup"]()
        fake = b"CreateProcessAsUserW failed error:1314\n"
        actual = b"error: unable to get security token for user runner [preauth]\r\n"
        window = fake + b"x" * (limit - len(fake) - len(actual) - 1) + b"\n" + actual
        startup.observe("server", io.BytesIO(b"untrusted prefix: " + window))
        self.assertEqual(startup.summary["server"], ["account-context"])
        startup.observe("server", io.BytesIO(b"complete preceding line\n" + window))
        self.assertEqual(startup.summary["server"], ["account-context", "required-privilege"])

    def test_listener_failure_records_only_pre_cleanup_observations(self):
        startup_type = FIXTURE["WindowsStartup"]
        cases = (
            (255, None, "process-exited", 255),
            (None, None, "timed-out", None),
            (None, OSError("PRIVATE_LISTENER_ERROR"), "listener-observation-error", None),
        )

        def wait(probe, _label):
            self.assertFalse(probe())
            raise AssertionError("PRIVATE_DEADLINE_ERROR")

        for status, failure, observation, exit_code in cases:
            with self.subTest(observation=observation):
                process = Mock(pid=42)
                process.poll.side_effect = [status, 0]
                startup = startup_type()
                with patch.dict(startup_type.wait_for_listener.__globals__, {
                    "listening": Mock(return_value=False, side_effect=failure),
                    "wait_until": wait,
                }), self.assertRaises(FIXTURE["StartupFailed"]):
                    with startup.stage("server-listener"):
                        startup.wait_for_listener(50325, process, "disposable server")
                process.poll.assert_called_once()
                self.assertEqual(process.poll(), 0)
                self.assertEqual(startup.summary["observation"], observation)
                self.assertEqual(startup.summary.get("exitCode"), exit_code)
                rendered = FIXTURE["render_evidence"](startup.summary)
                self.assertIn(f"observation={observation}", rendered)
                self.assertEqual("; exit=255" in rendered, exit_code == 255)
                self.assertNotIn("PRIVATE_", rendered)

    def test_adversarial_lines_and_evidence_file_never_echo(self):
        marker = b"DO NOT DISPLAY this private key or environment value"
        fake = (
            marker + b": CreateProcessAsUserW failed error:1314\n"
            b"CreateProcessAsUserW failed error:13140\n"
            b"banner\rerror: unable to get security token for user runner\n"
            b"fatal: \x1b[31mCreateProcessAsUserW failed error:1314\n"
            b"debug1: " + b"X" * 600 + b" Failed publickey for runner from 127.0.0.1 port 50325 ssh2\n"
            + b"X" * FIXTURE["EVIDENCE_LIMIT"] + b"\nCreateProcessAsUserW failed error:1314\n"
        )
        self.assertEqual(classify(fake, "server"), ["unidentified"])
        self.assertEqual(version(b"fake OpenSSH_for_Windows_99.1\n"), "unidentified")
        self.assertEqual(version(b"sshd: unknown option -- V\nOpenSSH_for_Windows_8.1p1, LibreSSL 3.0\n"), "8.1p1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "startup-evidence.json"
            payload = {
                "phase": "server-listener", "config": "accepted", "clientVersion": "9.5p1",
                "serverVersion": "9.5p1", "client": ["unidentified"], "server": ["required-privilege"],
                "observation": "process-exited", "exitCode": 255,
                "arbitrary": marker.decode(),
            }
            path.write_text(json.dumps(payload), encoding="ascii")
            output = read_evidence(root)
            self.assertIn("server System32/OpenSSH/sshd.exe: version=9.5p1; evidence=required-privilege", output)
            self.assertNotIn(marker.decode(), output)
            for key in ("phase", "config", "clientVersion", "server", "observation", "exitCode"):
                with self.subTest(key=key):
                    path.write_text(json.dumps({**payload, key: marker.decode()}), encoding="ascii")
                    self.assertEqual(read_evidence(root), "Windows OpenSSH fixture startup evidence unavailable")
            for invalid_exit in (True, 0x100000000):
                path.write_text(json.dumps({**payload, "exitCode": invalid_exit}), encoding="ascii")
                self.assertEqual(read_evidence(root), "Windows OpenSSH fixture startup evidence unavailable")
            path.write_text(json.dumps({**payload, "arbitrary": "X" * FIXTURE["EVIDENCE_LIMIT"]}), encoding="ascii")
            self.assertEqual(read_evidence(root), "Windows OpenSSH fixture startup evidence unavailable")

    def test_config_failure_reports_observed_outcome_and_prevents_spawn(self):
        windows_server = FIXTURE["windows_server"]
        report = FIXTURE["report_windows_startup"]
        calls = []
        marker = "PRIVATE_CONFIG_PATH"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "System32" / "OpenSSH"
            tools.mkdir(parents=True)
            for name in ("ssh", "sshd", "ssh-keygen"):
                (tools / (name + ".exe")).touch()

            def run(argv, **options):
                calls.append(argv)
                if argv[1:] == ["-V"]:
                    options["stderr"].write(b"OpenSSH_for_Windows_9.5p1, LibreSSL 3.8.2\n")
                elif argv[0].name == "whoami.exe":
                    options["stdout"].write(b"STOCKHOST\\LocalRunner\r\n")
                elif argv[0].name == "ssh-keygen.exe":
                    argv[-1].with_suffix(".pub").write_text("ssh-ed25519 placeholder\n", encoding="ascii")
                elif argv[0].name == "sshd.exe":
                    self.assertEqual(argv[1:4], ["-t", "-e", "-f"])
                    self.assertIn("AllowUsers localrunner\n", argv[-1].read_text(encoding="utf-8"))
                    if config_outcome == "rejected":
                        options["stderr"].write(
                            f"{marker}/sshd.config: line 18: Bad configuration option: Unknown\n".encode()
                        )
                        raise subprocess.CalledProcessError(255, argv)
                    if config_outcome == "timed-out":
                        raise subprocess.TimeoutExpired(argv, 10)
                    raise OSError(marker)

            with patch.dict(windows_server.__wrapped__.__globals__, {
                "openssh": lambda name: tools / (name + ".exe"),
                "reserve_port": lambda: nullcontext(50325),
            }), patch("socket.gethostname", return_value="STOCKHOST.example"), patch("subprocess.run", side_effect=run), patch(
                "subprocess.Popen", side_effect=AssertionError("server launched despite rejected config")
            ) as spawn:
                for config_outcome, observation in (
                    ("rejected", "process-exited"), ("timed-out", "timed-out"), ("error", "error"),
                ):
                    with self.subTest(config_outcome=config_outcome):
                        calls.clear()
                        with self.assertRaisesRegex(SystemExit, "fixture startup failed"):
                            with report(root) as startup, windows_server(root, startup):
                                self.fail("failed configuration validation yielded a server")
                        grants = [argv for argv in calls if argv[0].name == "icacls.exe"]
                        self.assertEqual(len(grants), 3)
                        self.assertTrue(all(r"stockhost\localrunner:(F)" in argv for argv in grants))
                        output = read_evidence(root)
                        self.assertIn(f"phase=server-config; sshd -t={config_outcome}; observation={observation}", output)
                        self.assertEqual("evidence=config-directive" in output, config_outcome == "rejected")
                        self.assertNotIn(marker, output)
                spawn.assert_not_called()
        account = FIXTURE["windows_account"]
        for invalid in (b"domain\\runner\n", b"stockhost\\runner *\n", b"stockhost\\runner\nextra\n"):
            with self.assertRaises(ValueError):
                account(invalid, "stockhost")


    def test_permission_rejections_match_only_exact_complete_fixture_paths(self):
        reports = FIXTURE["permission_reports"]
        paths = {
            "authorized-keys": r"C:\Fixture Space\authorized_keys", "host-key": r"C:\Fixture Space\host_key",
            "fixture-directory": r"C:\Fixture Space",
        }
        sid = "S-1-5-21-1-2-3-1001"
        data = (
            f"debug3: Bad permissions. Try removing permissions for user: PRIVATE ({sid}) on file "
            "\\\\?\\c:\\FIXTURE SPACE\\authorized_keys. [preauth]\r\n"
            "debug3: Bad owner on C:/fixture space/host_key\r\n"
            "error: Authentication refused: bad ownership or modes for directory C:/fixture space\n"
            "error: Authentication refused: bad ownership or modes for file C:/wrong/authorized_keys\n"
            "debug3: Bad owner on C:/fixture space/host_key.bak\n"
            "debug3: failed to retrieve the owner sid and dacl of file C:/fixture space/host_key with error code: 5"
        ).encode("ascii")
        self.assertEqual(reports(data, paths)[:2], (["bad-ownership-or-modes", "rejected-ace", "rejected-owner"], {
            "authorized-keys": {"reason": "rejected-ace", "pathForm": "extended-length", "sid": sid},
            "host-key": {"reason": "rejected-owner", "pathForm": "drive", "sid": None},
            "fixture-directory": {"reason": "bad-ownership-or-modes", "pathForm": "drive", "sid": None},
        }))
        self.assertEqual(reports(
            b"error: Could not open authorized keys 'C:/Fixture Space/authorized_keys': Permission denied\n", paths,
        ), (["access-denied"], {"authorized-keys": {"reason": "access-denied", "pathForm": "drive", "sid": None}}, []))
        self.assertFalse(reports(data, {"authorized-keys": r"C:\another\authorized_keys"})[1])

    def test_permission_templates_do_not_turn_labels_or_aliases_into_path_attribution(self):
        reports = FIXTURE["permission_reports"]
        paths = {"authorized-keys": r"D:\ProgramData\Fixture Space\authorized_keys"}
        for path, form in (
            ('"/D:/ProgramData/Fixture Space/authorized_keys"', "openssh-realpath"),
            ("/cygdrive/d/ProgramData/Fixture Space/authorized_keys", "cygdrive"),
            (r"__PROGRAMDATA__\\Fixture Space\authorized_keys", "program-data"),
            (r"d:\\ProgramData\\Fixture Space\\authorized_keys", "repeated-separators"),
        ):
            with self.subTest(form=form):
                self.assertEqual(reports(
                    f"debug3: Bad owner on {path} [preauth]\r\n".encode(), paths, r"D:\ProgramData",
                ), (["rejected-owner"], {"authorized-keys": {
                    "reason": "rejected-owner", "pathForm": form, "sid": None,
                }}, []))
        self.assertEqual(reports(
            b"Authentication refused. [preauth]\r\n"
            b"debug1: Ignored authorized keys [preauth]\r\n"
            b"Ignored authorized keys: bad ownership or modes\n"
            b"Ignored /cygdrive/e/ProgramData/Fixture Space/authorized_keys: bad ownership or modes\n"
            b"Bad owner on __PROGRAMDATA__/Fixture Space/authorized_keys\n",
            paths,
        )[:2], (["auth-strict-refused", "ignored-authorized-keys", "ignored-path-modes", "rejected-owner"], {}))

    def test_unmatched_permission_paths_emit_roles_and_shape_without_private_components(self):
        reports = FIXTURE["permission_reports"]
        paths = {"fixture-directory": r"D:\private-root", "authorized-keys": r"D:\private-root\authorized_keys"}
        templates, attributed, shapes = reports((
            b"Bad owner on D:/private-root/private-child/authorized_keys\n"
            b"Bad owner on E:/extra-private/private-root/authorized_keys\n"
            b"Bad owner on __PROGRAMDATA__/ssh/administrators_authorized_keys\n"
            b"Bad owner on private-component/private-filename\n"
            b"Bad owner on extra-unique-fifth\n"
        ), paths, r"D:\ProgramData")
        self.assertEqual(templates, ["rejected-owner"])
        self.assertFalse(attributed)
        self.assertEqual([(s["basename"], s["location"], s["components"]) for s in shapes], [
            ("authorized-keys", "within-fixture-root", 3),
            ("authorized-keys", "fixture-directory-component", 3),
            ("default-admin-key-list", "outside-fixture-root", 3),
            ("other", "relative", 2),
        ])
        self.assertNotIn("private-", json.dumps(shapes))

    def test_observation_correlates_a_sanitized_raw_descriptor_or_marks_it_unavailable(self):
        inspect = FIXTURE["inspect_permissions"]
        startup_type = FIXTURE["WindowsStartup"]
        owner = ["token-user", "login-user", "sshd-listener-user"]
        descriptor = {
            "owner": owner, "control": 0x8404, "aceCount": 2, "reportedSidMatches": 1,
            "aces": [
                {"index": 0, "type": 0, "flags": 0, "mask": "001f01ff",
                 "principal": owner, "matchesReportedSid": False},
                {"index": 1, "type": 0, "flags": 0x10, "mask": "001301bf",
                 "principal": ["token-group", "builtin-users"], "matchesReportedSid": True},
            ],
        }
        sid = "S-1-5-21-1-2-3-1001"
        login = r"private-host\private-user"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data = (
                f"Bad permissions. Try removing permissions for user: {login} ({sid}) "
                f"on file {root / 'authorized_keys'}.\n"
            ).encode("ascii")
            payload = {
                "status": "completed",
                "identities": dict.fromkeys(owner, owner),
                "files": {"authorized-keys": {"status": "read", "descriptor": descriptor}},
            }

            expected_paths = {"authorized-keys": sid}

            def run(argv, **options):
                self.assertEqual(argv[0].name, "powershell.exe")
                self.assertEqual(argv[-1].name, "inspect-managed-check-acl.ps1")
                self.assertIsNotNone(options["stderr"])
                self.assertEqual(json.loads(options["input"]), {
                    "root": str(root), "login": login, "serverPid": 41,
                    "paths": expected_paths,
                    "limit": FIXTURE["ACE_LIMIT"],
                })
                options["stdout"].write(json.dumps(payload).encode("ascii"))

            process = Mock(pid=41)
            process.poll.return_value = None
            startup = startup_type(root)
            with patch.dict(inspect.__globals__, {"openssh": lambda name: root / "OpenSSH" / (name + ".exe")}), \
                    patch("subprocess.run", side_effect=run) as run_inspector:
                startup.observe("server", io.BytesIO(data), root=root, login=login, server_process=process)
                startup.publish()
                rendered = read_evidence(root)
                self.assertIn("authorized-keys: rejection=rejected-ace; path-form=", rendered)
                self.assertIn("owner=token-user+login-user+sshd-listener-user", rendered)
                self.assertIn("protected=no; auto-inherited=yes; dacl=2", rendered)
                self.assertIn("reported-sid-ace-matches=1", rendered)
                self.assertIn("(explicit); mask=0x001f01ff", rendered)
                self.assertIn(
                    "(inherited); mask=0x001301bf; principal=token-group+builtin-users; matches-reported-sid=yes",
                    rendered,
                )
                self.assertIn("auth-child-token=not-observed", rendered)
                raw = (root / "startup-evidence.json").read_text(encoding="ascii").lower()
                for private in (login.split("\\")[0], login.split("\\")[1], sid, root.name):
                    self.assertNotIn(private.lower(), raw)
                    self.assertNotIn(private.lower(), rendered.lower())

                descriptor["untrusted"] = sid
                startup.observe("server", io.BytesIO(data), root=root, login=login, server_process=process)
                startup.publish()
                self.assertIn("permission inspector: invalid-output", read_evidence(root))
                self.assertNotIn(sid, (root / "startup-evidence.json").read_text(encoding="ascii"))

                run_inspector.reset_mock()
                expected_paths = dict.fromkeys(("authorized-keys", "host-key", "client-key"))
                payload["files"] = dict.fromkeys(expected_paths, {"status": "read", "descriptor": {
                    "owner": owner, "control": 0x9004, "aceCount": 0, "reportedSidMatches": None, "aces": [],
                }})
                payload["files"]["client-key"] = {"status": "missing", "descriptor": None}
                startup.observe("server", io.BytesIO(b"debug3: Bad owner on C:/outside/authorized_keys\n"),
                                root=root, login=login, server_process=process)
                startup.publish()
                run_inspector.assert_called_once()
                rendered = read_evidence(root)
                self.assertIn("permission templates: rejected-owner", rendered)
                self.assertIn("permission inspector: completed", rendered)
                self.assertIn("basename-role=authorized-keys; location=outside-fixture-root", rendered)
                self.assertIn("no known fixture path matched; snapshots are not attribution", rendered)
                for role in expected_paths:
                    self.assertIn(f"descriptor {role}: rejection=not-attributed; snapshot=after-rejection", rendered)
                self.assertIn("snapshot=after-rejection; descriptor-read=missing", rendered)
                self.assertNotIn("C:/outside", rendered)

    def test_inspector_classifies_failure_without_rendering_stderr_or_paths(self):
        inspect = FIXTURE["inspect_permissions"]
        marker = b"private inspector error"
        startup = FIXTURE["WindowsStartup"]()

        def run(argv, **options):
            if error is None:
                options["stdout"].write(output)
                return
            options["stderr"].write(output)
            if error is OSError:
                raise OSError(marker)
            raise error(1, argv)

        for error, output, status in (
            (None, b'{"status":"fixture-root-unavailable"}', "fixture-root-unavailable"),
            (None, marker, "invalid-output"),
            (subprocess.CalledProcessError, marker + b" PSSecurityException", "execution-policy"),
            (subprocess.CalledProcessError, marker, "process-failed"),
            (subprocess.TimeoutExpired, marker, "timed-out"),
            (OSError, marker, "launch-failed"),
        ):
            with self.subTest(status=status), patch.dict(inspect.__globals__, {
                "openssh": lambda name: Path("System32/OpenSSH") / (name + ".exe"),
            }), patch("subprocess.run", side_effect=run):
                startup.summary["permissions"] = inspect(
                    Path("private-root"), "private-login", None, ["rejected-owner"], {}, [],
                )
                rendered = FIXTURE["render_evidence"](startup.summary)
                self.assertIn(f"permission inspector: {status}", rendered)
                self.assertNotIn("private", rendered)

    def test_authorized_path_proof_requires_same_parent_and_file(self):
        prove = FIXTURE["same_authorized_file"]
        root = PureWindowsPath(r"C:\Fixture Parent\Fixture Child")
        alias = r"c:\fixtur~1\fixture child\authorized_keys"
        with patch("os.path.samefile", side_effect=[True, True]) as same:
            self.assertTrue(prove(root, alias))
            self.assertEqual(same.call_args_list[0].args, (r"c:\fixtur~1\fixture child", root))
            self.assertEqual(same.call_args_list[1].args, (alias, root / "authorized_keys"))
        with patch("os.path.samefile", return_value=False) as same:
            self.assertFalse(prove(root, alias))
            same.assert_called_once()
        for other in (r"\\host\share\authorized_keys", r"d:\fixture\authorized_keys", r"c:\fixture\..\authorized_keys",
                      r"c:\fixture\authorized_keys:alternate", r"c:\fixture\host_key"):
            with self.subTest(other=other), patch("os.path.samefile") as same:
                self.assertFalse(prove(root, other))
                same.assert_not_called()

    def test_correction_report_requires_one_sid_from_the_proven_file(self):
        parse = FIXTURE["permission_reports"]
        paths = {"authorized-keys": r"C:\Fixture\authorized_keys"}
        verified = r"c:\fixtur~1\authorized_keys"

        def rejection(path, rid):
            return f"Bad permissions. Try removing permissions for user: other (S-1-5-21-1-2-3-{rid}) on file {path}.\n"

        one = rejection(verified, 1001)
        for evidence, accepted in (
            (one + one + rejection(paths["authorized-keys"], 1002), True),
            (one + rejection(verified, 1002), False),
            (rejection(paths["authorized-keys"], 1002), False),
        ):
            _, reports, _ = parse(evidence.encode(), paths, prove_authorized=lambda path: path == verified)
            self.assertEqual(reports.get("authorized-keys"), {
                "reason": "rejected-ace", "pathForm": "drive", "sid": "S-1-5-21-1-2-3-1001",
            } if accepted else None)

    def test_fixture_identity_rejects_hard_links(self):
        file_id = FIXTURE["fixture_file_id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorized_keys"
            path.write_text("first", encoding="ascii")
            before = file_id(path)
            path.write_text("second", encoding="ascii")
            self.assertEqual(file_id(path), before)
            path.with_suffix(".alias").hardlink_to(path)
            with self.assertRaises(ValueError):
                file_id(path)

    def test_only_proven_independent_authentication_refusal_gets_one_retry(self):
        independent = FIXTURE["independent"]
        failed = FIXTURE["StartupFailed"]
        for client, corrected, retry_fails, attempts in (
            (["authentication-rejected"], True, False, 2),
            (["authentication-rejected"], True, True, 2),
            (["authentication-rejected"], False, False, 1),
            (["authentication-rejected", "host-verification"], True, False, 1),
            (["forwarding-denied"], True, False, 1),
        ):
            with self.subTest(client=client, corrected=corrected, retry_fails=retry_fails):
                startup = FIXTURE["WindowsStartup"]()
                startup.correct_independent = Mock(return_value=corrected)
                calls = []

                @contextmanager
                def attempt(*_):
                    calls.append(None)
                    if len(calls) == 1 or retry_fails:
                        startup.summary.update(client=client, observation="process-exited", exitCode=255)
                        raise failed
                    self.assertNotIn("exitCode", startup.summary)
                    yield "owned-forward"

                with patch.dict(independent.__wrapped__.__globals__, {"independent_attempt": attempt}):
                    outcome = nullcontext() if attempts == 2 and not retry_fails else self.assertRaises(failed)
                    with outcome, independent(None, None, None, None, startup) as result:
                        self.assertEqual(result, "owned-forward")
                self.assertEqual(len(calls), attempts)
                self.assertEqual(startup.correct_independent.call_count, int(client == ["authentication-rejected"]))

    def test_independent_relay_probe_requires_the_live_health_response(self):
        probe = FIXTURE["probe_relay"]
        cases = (
            (200, b'{"feed":"live","has_snapshot":true,"seq":2}', None),
            (200, b'{"feed":"stale","has_snapshot":true,"seq":2}', AssertionError),
            (200, b'{"feed":"live","has_snapshot":false,"seq":2}', AssertionError),
            (200, b'{"feed":"live","has_snapshot":true,"seq":1}', AssertionError),
            (200, b"", AssertionError),
            (200, b"x" * 1025, AssertionError),
            (404, b'{"feed":"live","has_snapshot":true,"seq":2}', AssertionError),
        )
        for status, body, error in cases:
            with self.subTest(status=status, body=body), patch("http.client.HTTPConnection") as connect:
                connection = connect.return_value
                response = connection.getresponse.return_value
                response.status = status
                response.read.return_value = body
                with self.assertRaises(error) if error else nullcontext():
                    probe(50325)
                connect.assert_called_once_with("127.0.0.1", 50325, timeout=3)
                connection.request.assert_called_once_with("GET", "/healthz")
                response.read.assert_called_once_with(1025)
                connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
