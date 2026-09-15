"""Run the TinyScry relay."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from .config import parse_args
from .server import RelayServer


async def _run() -> None:
    config = parse_args()
    logging.basicConfig(
        level=getattr(logging, config.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if config.allow_non_loopback and config.host not in {"127.0.0.1", "::1", "localhost"}:
        logging.getLogger(__name__).warning(
            "BINDING RELAY OUTSIDE LOOPBACK: exposed relay has no authentication"
        )
    relay = RelayServer(
        host=config.host,
        port=config.port,
        stale_after=config.stale_after,
        allow_non_loopback=config.allow_non_loopback,
    )
    await relay.start()
    logging.getLogger(__name__).info("relay listening on %s:%s", config.host, relay.port)
    try:
        await asyncio.Future[None]()
    finally:
        await relay.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
