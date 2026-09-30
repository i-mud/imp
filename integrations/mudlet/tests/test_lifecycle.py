from __future__ import annotations

from imp_adapter.records import Record
from imp_relay.protocol import StateContext

from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import (
    ConnectedMessage,
    DisconnectedMessage,
    FocusMessage,
    GmcpMessage,
    InitMessage,
    ProtocolMessage,
)

SESSION = "mudlet_deadbeef"


def test_initial_connected_focused_profile_gets_first_context() -> None:
    lifecycle = MudletLifecycle(SESSION)

    lifecycle.apply(InitMessage("AVATAR", True, True))

    assert lifecycle.context == StateContext(SESSION, 1, 1)


def test_repeated_connection_and_focus_true_are_idempotent() -> None:
    lifecycle = MudletLifecycle(SESSION)
    lifecycle.apply(InitMessage("AVATAR", True, True))

    lifecycle.apply(ConnectedMessage())
    lifecycle.apply(FocusMessage(True))

    assert lifecycle.connection == 1
    assert lifecycle.foreground == 1


def test_reconnect_advances_only_connection_generation() -> None:
    lifecycle = MudletLifecycle(SESSION)
    lifecycle.apply(InitMessage("AVATAR", True, True))

    lifecycle.apply(DisconnectedMessage())
    assert lifecycle.context is None

    lifecycle.apply(ConnectedMessage())

    assert lifecycle.context == StateContext(SESSION, 1, 2)


def test_refocus_advances_only_foreground_generation() -> None:
    lifecycle = MudletLifecycle(SESSION)
    lifecycle.apply(InitMessage("AVATAR", True, True))

    lifecycle.apply(FocusMessage(False))
    assert lifecycle.context is None

    lifecycle.apply(FocusMessage(True))

    assert lifecycle.context == StateContext(SESSION, 2, 1)


def test_disconnect_clears_gmcp_readiness() -> None:
    lifecycle = MudletLifecycle(SESSION)
    lifecycle.apply(InitMessage("AVATAR", True, True))
    lifecycle.apply(ProtocolMessage("GMCP", True))

    assert lifecycle.gmcp_enabled

    lifecycle.apply(DisconnectedMessage())

    assert not lifecycle.gmcp_enabled


def test_observed_gmcp_is_itself_readiness_evidence() -> None:
    lifecycle = MudletLifecycle(SESSION)
    lifecycle.apply(InitMessage("AVATAR", True, True))

    lifecycle.apply(
        GmcpMessage(
            Record(
                at=1234,
                package="Char.Status",
                payload={"character_name": "Ariadne"},
            )
        )
    )

    assert lifecycle.gmcp_enabled
