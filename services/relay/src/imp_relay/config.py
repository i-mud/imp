"""Command-line configuration for the loopback relay."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RelayConfig:
    host: str
    port: int
    stale_after: float
    log_level: str


def parse_args(argv: list[str] | None = None) -> RelayConfig:
    parser = argparse.ArgumentParser(description="Imp loopback state relay")
    parser.add_argument("--host", default=os.environ.get("IMP_RELAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_port())
    parser.add_argument("--stale-after", type=float, default=10.0, metavar="SECONDS")
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    )
    parsed = parser.parse_args(argv)
    if not 0 < parsed.port <= 65_535:
        parser.error("--port must be in 1..65535")
    if parsed.stale_after <= 0:
        parser.error("--stale-after must be positive")
    if parsed.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("--host must resolve to a loopback binding")
    return RelayConfig(
        host=parsed.host,
        port=parsed.port,
        stale_after=parsed.stale_after,
        log_level=parsed.log_level,
    )


def _env_port() -> int:
    raw_port = os.environ.get("IMP_RELAY_PORT", "8787")
    try:
        return int(raw_port)
    except ValueError as error:
        raise ValueError("IMP_RELAY_PORT must be an integer") from error
