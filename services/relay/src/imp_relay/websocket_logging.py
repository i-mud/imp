"""Credential-safe diagnostics for Imp-owned WebSocket connections."""

import logging

LOGGER = logging.getLogger("imp_relay.websocket")


def websocket_logger() -> logging.Logger:
    # DEBUG includes raw auth frames. Retain earlier restrictions across starts.
    LOGGER.setLevel(
        max(
            logging.INFO,
            LOGGER.getEffectiveLevel(),
            *(
                logging.getLogger(name).getEffectiveLevel()
                for name in ("", "imp_relay", "websockets", "websockets.server", "websockets.client")
            ),
        )
    )
    return LOGGER
