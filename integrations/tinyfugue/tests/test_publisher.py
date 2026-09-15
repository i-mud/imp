from __future__ import annotations

import asyncio

import pytest
from tinyscry_relay.protocol import Character, GameState, Target, Vital

from tinyscry_tf.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire


def _state(name: str = "Ariadne", percent: float | None = 50.0) -> GameState:
    return GameState(
        character=Character(name=name, hp=Vital(current=10, max=20), mana=None, moves=None),
        target=Target(name="a cave troll", health_percent=percent),
    )


def test_wire_form_uses_the_protocol_field_spelling() -> None:
    wire = state_to_wire(_state())

    assert wire == {
        "character": {"name": "Ariadne", "hp": {"current": 10, "max": 20}, "mana": None, "moves": None},
        "target": {"name": "a cave troll", "healthPercent": 50.0},
    }


def test_publishing_invalid_state_fails_before_any_connection() -> None:
    """
    The publisher validates its own output so a normalization bug surfaces here
    rather than as a relay-side rejection that closes the ingest socket. The
    URL points at a port with nothing on it: if validation did not run first,
    this would hang retrying the connection instead of raising.
    """
    publisher = RelayPublisher(url="ws://127.0.0.1:1/ingest")
    # A percentage above 100 violates the protocol bound.
    with pytest.raises(ValueError, match=r"state\.target\.healthPercent"):
        asyncio.run(asyncio.wait_for(publisher.publish(_state(percent=140.0)), timeout=2))


def test_non_loopback_target_is_refused_without_an_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow-non-loopback"):
        RelayPublisher(url="ws://203.0.113.7:8787/ingest")

    # The opt-in exists, but it must be asked for.
    RelayPublisher(url="ws://203.0.113.7:8787/ingest", allow_non_loopback=True)
    assert RelayPublisher().url == DEFAULT_RELAY_URL
