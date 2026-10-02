"""Mudlet profile lifecycle mapped onto Imp context generations."""

from __future__ import annotations

from dataclasses import dataclass

from imp_relay.protocol import StateContext

from imp_mudlet.protocol import (
    ConnectedMessage,
    DisconnectedMessage,
    FocusMessage,
    GmcpMessage,
    InitMessage,
    LuaMessage,
    ProtocolMessage,
)


@dataclass
class MudletLifecycle:
    session: str
    initialized: bool = False
    profile: str | None = None
    connected: bool = False
    focused: bool = False
    gmcp_enabled: bool = False
    connection: int = 0
    foreground: int = 0

    @property
    def context(self) -> StateContext | None:
        if not self.initialized or not self.connected or not self.focused:
            return None
        if self.connection < 1 or self.foreground < 1:
            return None
        return StateContext(self.session, self.foreground, self.connection)

    def apply(self, message: LuaMessage) -> None:
        if isinstance(message, InitMessage):
            if self.initialized:
                raise ValueError("Mudlet lifecycle is already initialized")
            self.initialized = True
            self.profile = message.profile
            self.connected = message.connected
            self.focused = message.focused
            if self.connected:
                self.connection = 1
            if self.focused:
                self.foreground = 1
            return

        if not self.initialized:
            raise ValueError("Mudlet lifecycle must be initialized first")

        if isinstance(message, ConnectedMessage):
            if not self.connected:
                self.connection += 1
            self.connected = True
            return

        if isinstance(message, DisconnectedMessage):
            self.connected = False
            self.gmcp_enabled = False
            return

        if isinstance(message, FocusMessage):
            if message.focused and not self.focused:
                self.foreground += 1
            self.focused = message.focused
            return

        if isinstance(message, ProtocolMessage):
            if message.name.casefold() == "gmcp":
                self.gmcp_enabled = message.enabled
            return

        if isinstance(message, GmcpMessage):
            # Receiving a GMCP frame itself proves that GMCP is usable. This
            # also makes installing/reloading the package mid-session recover
            # even if sysProtocolEnabled happened before the package loaded.
            # A late frame after disconnect must not resurrect readiness.
            if self.connected:
                self.gmcp_enabled = True
            return

        raise TypeError(f"unsupported Mudlet message: {type(message)!r}")
