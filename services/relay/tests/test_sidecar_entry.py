from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from imp_relay.__main__ import main as relay_main
from imp_relay.gateway_main import main as gateway_main

SIDECAR_ENTRY = Path(__file__).resolve().parents[1] / "sidecar_entry.py"

spec = importlib.util.spec_from_file_location("imp_desktop_sidecar_entry", SIDECAR_ENTRY)
assert spec is not None
assert spec.loader is not None

sidecar_entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecar_entry)

_dispatch = sidecar_entry._dispatch


def test_sidecar_defaults_to_relay_for_legacy_arguments() -> None:
    argv = ["--host", "127.0.0.1", "--port", "8787"]

    handler, forwarded = _dispatch(argv)

    assert handler is relay_main
    assert forwarded == argv


def test_sidecar_defaults_to_relay_with_no_arguments() -> None:
    handler, forwarded = _dispatch([])

    assert handler is relay_main
    assert forwarded == []


def test_sidecar_dispatches_explicit_relay_mode() -> None:
    handler, forwarded = _dispatch(["relay", "--host", "127.0.0.1", "--port", "8787"])

    assert handler is relay_main
    assert forwarded == ["--host", "127.0.0.1", "--port", "8787"]


def test_sidecar_dispatches_gateway_mode() -> None:
    handler, forwarded = _dispatch(
        [
            "gateway",
            "--host",
            "127.0.0.1",
            "--port",
            "8788",
            "--token-sha256",
            "00" * 32,
        ]
    )

    assert handler is gateway_main
    assert forwarded == [
        "--host",
        "127.0.0.1",
        "--port",
        "8788",
        "--token-sha256",
        "00" * 32,
    ]


def test_sidecar_rejects_unknown_positional_mode() -> None:
    with pytest.raises(SystemExit, match="unknown imp-node mode: nope"):
        _dispatch(["nope"])
