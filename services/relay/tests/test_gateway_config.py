from __future__ import annotations

import hashlib

import pytest

from tinyscry_relay.gateway_config import parse_gateway_args

TOKEN = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_DIGEST_HEX = hashlib.sha256(TOKEN.encode("ascii")).hexdigest()


def test_gateway_config_defaults_to_loopback() -> None:
    config = parse_gateway_args(["--token-sha256", TOKEN_DIGEST_HEX])

    assert config.host == "127.0.0.1"
    assert config.port == 8788
    assert config.relay_url == "ws://127.0.0.1:8787"
    assert config.auth_timeout == 3.0
    assert config.token_sha256 == bytes.fromhex(TOKEN_DIGEST_HEX)


def test_gateway_config_reads_digest_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TINYSCRY_GATEWAY_TOKEN_SHA256", TOKEN_DIGEST_HEX)

    config = parse_gateway_args([])

    assert config.token_sha256 == bytes.fromhex(TOKEN_DIGEST_HEX)


def test_gateway_config_requires_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TINYSCRY_GATEWAY_TOKEN_SHA256", raising=False)

    with pytest.raises(SystemExit):
        parse_gateway_args([])


@pytest.mark.parametrize(
    "args",
    [
        ["--host", "0.0.0.0"],
        ["--host", "localhost"],
        ["--port", "0"],
        ["--relay-url", "ws://example.com:8787"],
        ["--relay-url", "ws://localhost:8787"],
        ["--relay-url", "wss://127.0.0.1:8787"],
        ["--auth-timeout", "0"],
        ["--token-sha256", "not-a-digest"],
    ],
)
def test_gateway_config_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    monkeypatch.setenv("TINYSCRY_GATEWAY_TOKEN_SHA256", TOKEN_DIGEST_HEX)

    with pytest.raises(SystemExit):
        parse_gateway_args(args)
