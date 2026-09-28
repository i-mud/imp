"""Command-line configuration for the authenticated Imp gateway."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass

from .gateway import validate_relay_url

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_DIGEST_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    relay_url: str
    auth_timeout: float
    token_sha256: bytes
    log_level: str


def _parse_token_digest(value: str) -> bytes:
    if _DIGEST_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("token digest must be exactly 64 hexadecimal characters")
    return bytes.fromhex(value)


def parse_gateway_args(argv: list[str] | None = None) -> GatewayConfig:
    parser = argparse.ArgumentParser(description="Imp authenticated remote gateway")
    parser.add_argument(
        "--host",
        default=os.environ.get("IMP_GATEWAY_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("IMP_GATEWAY_PORT", "8788"),
    )
    parser.add_argument(
        "--relay-url",
        default=os.environ.get(
            "IMP_GATEWAY_RELAY_URL",
            "ws://127.0.0.1:8787",
        ),
    )
    parser.add_argument(
        "--auth-timeout",
        type=float,
        default=os.environ.get("IMP_GATEWAY_AUTH_TIMEOUT", "3.0"),
        metavar="SECONDS",
    )
    parser.add_argument(
        "--token-sha256",
        type=_parse_token_digest,
        default=os.environ.get("IMP_GATEWAY_TOKEN_SHA256"),
        metavar="HEX",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )

    parsed = parser.parse_args(argv)

    if parsed.host not in _LOOPBACK_HOSTS:
        parser.error("--host must resolve to a loopback binding")
    if not 0 < parsed.port <= 65_535:
        parser.error("--port must be in 1..65535")
    if parsed.auth_timeout <= 0:
        parser.error("--auth-timeout must be positive")
    if parsed.token_sha256 is None:
        parser.error("--token-sha256 or IMP_GATEWAY_TOKEN_SHA256 is required")

    try:
        relay_url = validate_relay_url(parsed.relay_url)
    except ValueError as error:
        parser.error(str(error))

    return GatewayConfig(
        host=parsed.host,
        port=parsed.port,
        relay_url=relay_url,
        auth_timeout=parsed.auth_timeout,
        token_sha256=parsed.token_sha256,
        log_level=parsed.log_level,
    )
