from __future__ import annotations

import hashlib

import pytest

from imp_relay.gateway_config import GatewayConfig, parse_gateway_args

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
    monkeypatch.setenv("IMP_GATEWAY_TOKEN_SHA256", TOKEN_DIGEST_HEX)

    config = parse_gateway_args([])

    assert config.token_sha256 == bytes.fromhex(TOKEN_DIGEST_HEX)


def test_gateway_config_requires_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IMP_GATEWAY_TOKEN_SHA256", raising=False)

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
    monkeypatch.setenv("IMP_GATEWAY_TOKEN_SHA256", TOKEN_DIGEST_HEX)

    with pytest.raises(SystemExit):
        parse_gateway_args(args)


@pytest.mark.parametrize("duration", [float("inf"), float("-inf"), float("nan"), 0.0, -1.0])
def test_gateway_config_rejects_nonpositive_or_nonfinite_auth_timeout(duration: float) -> None:
    with pytest.raises(ValueError):
        GatewayConfig(
            "127.0.0.1", 8788, "ws://127.0.0.1:8787", duration, bytes.fromhex(TOKEN_DIGEST_HEX), "INFO"
        )


@pytest.mark.parametrize("duration", ["inf", "-inf", "nan", "0", "-1"])
@pytest.mark.parametrize("environment", [False, True])
def test_gateway_config_rejects_invalid_duration_from_cli_or_environment(
    monkeypatch: pytest.MonkeyPatch, duration: str, environment: bool
) -> None:
    monkeypatch.setenv("IMP_GATEWAY_TOKEN_SHA256", TOKEN_DIGEST_HEX)
    monkeypatch.delenv("IMP_GATEWAY_AUTH_TIMEOUT", raising=False)
    args = [f"--auth-timeout={duration}"]
    if environment:
        monkeypatch.setenv("IMP_GATEWAY_AUTH_TIMEOUT", duration)
        args = []

    with pytest.raises(SystemExit) as rejected:
        parse_gateway_args(args)
    assert rejected.value.code == 2
