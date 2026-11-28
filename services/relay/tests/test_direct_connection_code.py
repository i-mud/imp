from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

import imp_relay.direct_wss as direct_wss

VECTORS = json.loads((Path(__file__).parents[3] / "tests/fixtures/direct-connection-codes.json").read_text())
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")


def _wire(endpoint: str, token: str) -> str:
    encoded = base64.urlsafe_b64encode(endpoint.encode()).rstrip(b"=").decode()
    return f"imp1.{encoded}.{token}"


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda vector: vector["name"])
def test_issuer_reproduces_trimmed_shared_valid_wire(vector: dict[str, str]) -> None:
    expected = vector["code"].strip(" \t\r\n")
    assert direct_wss._connection_code(vector["endpoint"], vector["token"]) == expected


@pytest.mark.parametrize(
    "vector",
    [vector for vector in VECTORS["invalid"] if "endpoint" in vector],
    ids=lambda vector: vector["name"],
)
def test_issuer_refuses_invalid_endpoints(vector: dict[str, str]) -> None:
    assert _wire(vector["endpoint"], TOKEN) == vector["code"]
    assert direct_wss._connection_code(vector["endpoint"], TOKEN) is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://imp.example/state/",
        "wss://imp.example:0443/state",
        "wss://imp.example:65536/state",
        "wss://imp.example/state?x",
        "ws://imp.example/state",
        "wss://imp.1/state",
        "wss://" + ".".join(["a" * 63] * 3 + ["b" * 62]) + "/state",
    ],
)
def test_issuer_refuses_further_unrepresentable_endpoints(endpoint: str) -> None:
    assert direct_wss._connection_code(endpoint, TOKEN) is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://imp/state",
        "wss://imp.example:65535/state",
        "wss://host1.example2/state",
        "wss://1a.b2/state",
        "wss://255.0.0.1:8443/state",
        "wss://" + ".".join(["a" * 63] * 3 + ["b" * 61]) + "/state",
    ],
)
def test_issuer_accepts_further_representable_endpoints(endpoint: str) -> None:
    code = direct_wss._connection_code(endpoint, TOKEN)
    assert code == _wire(endpoint, TOKEN)
    assert len(code) <= 512


def test_non_canonical_token_is_a_fault_not_a_fallback() -> None:
    with pytest.raises(direct_wss.DirectWssError):
        direct_wss._connection_code("wss://imp.example/state", TOKEN[:-1])


def test_handoff_prints_code_then_standalone_token() -> None:
    out = io.StringIO()
    code = _wire("wss://imp.example/state", TOKEN)
    direct_wss._write_pairing_token(TOKEN, out, code)
    lines = out.getvalue().splitlines()
    assert lines[1] == code
    assert lines[-2:] == ["Direct WSS pairing token (shown once; not stored):", TOKEN]


def test_handoff_without_code_is_unchanged() -> None:
    out = io.StringIO()
    direct_wss._write_pairing_token(TOKEN, out)
    assert out.getvalue() == f"Direct WSS pairing token (shown once; not stored):\n{TOKEN}\n"
