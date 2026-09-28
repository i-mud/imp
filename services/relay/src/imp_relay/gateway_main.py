"""Run the Imp authenticated remote gateway."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from .gateway import GatewayServer
from .gateway_config import parse_gateway_args


async def _run() -> None:
    config = parse_gateway_args()

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    gateway = GatewayServer(
        config.token_sha256,
        host=config.host,
        port=config.port,
        relay_url=config.relay_url,
        auth_timeout=config.auth_timeout,
    )
    await gateway.start()

    logging.getLogger(__name__).info(
        "gateway listening on %s:%s",
        config.host,
        gateway.port,
    )

    try:
        await asyncio.Future[None]()
    finally:
        await gateway.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_run())


if __name__ == "__main__":
    main()
