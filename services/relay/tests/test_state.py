from __future__ import annotations

from tinyscry_relay.protocol import GameState
from tinyscry_relay.state import RelayState


def test_sequence_is_monotonic_and_snapshot_is_retained_after_disconnect() -> None:
    state = RelayState(clock=lambda: 10.0)
    empty = GameState(character=None, target=None)

    state.producer_connected()
    first = state.apply_publish(empty, now=3.0)
    second = state.apply_publish(empty, now=4.0)
    state.producer_disconnected()

    assert (first.seq, second.seq) == (1, 2)
    assert state.snapshot() == second
    assert state.feed_status(now=4.0) == "down"


def test_feed_transitions_from_down_to_live_to_stale_and_back_down() -> None:
    state = RelayState(stale_after=10.0)
    empty = GameState(character=None, target=None)

    assert state.feed_status(now=0.0) == "down"
    state.producer_connected()
    assert state.feed_status(now=0.0) == "stale"
    state.apply_publish(empty, now=0.0)
    assert state.feed_status(now=9.99) == "live"
    assert state.feed_status(now=10.0) == "stale"
    state.producer_disconnected()
    assert state.feed_status(now=10.0) == "down"


def test_health_payload_reports_feed_and_retained_snapshot() -> None:
    now = 100.0
    state = RelayState(clock=lambda: now)
    empty = GameState(character=None, target=None)

    assert state.health() == {"feed": "down", "producer_count": 0, "has_snapshot": False, "seq": None}
    state.producer_connected()
    state.apply_publish(empty, now=now)

    assert state.health() == {"feed": "live", "producer_count": 1, "has_snapshot": True, "seq": 1}
