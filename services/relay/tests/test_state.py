from __future__ import annotations

from tinyscry_relay.protocol import GameState, StateContext
from tinyscry_relay.state import RelayState

EMPTY = GameState(character=None, target=None)
CONTEXT = StateContext("session1", 1, 1)


def test_sequence_is_monotonic_and_snapshot_is_retained_after_disconnect() -> None:
    state = RelayState(clock=lambda: 10.0)
    state.producer_connected()
    selected = state.apply_select(CONTEXT, EMPTY, now=2.0)
    first = state.apply_publish(CONTEXT, EMPTY, now=3.0)
    second = state.apply_publish(CONTEXT, EMPTY, now=4.0)
    state.producer_disconnected()

    assert first is not None and second is not None
    assert (selected.seq, first.seq, second.seq) == (1, 2, 3)
    assert state.snapshot() == second
    assert state.feed_status(now=4.0) == "down"


def test_publish_only_updates_the_selected_context() -> None:
    state = RelayState()
    other = StateContext("session1", 2, 1)
    selected = state.apply_select(CONTEXT, EMPTY, now=1.0)

    assert state.apply_publish(other, EMPTY, now=2.0) is None
    assert state.snapshot() == selected


def test_selection_invalidates_freshness_until_matching_publish() -> None:
    state = RelayState(stale_after=10.0)
    state.producer_connected()
    assert state.feed_status(now=0.0) == "stale"

    state.apply_select(CONTEXT, EMPTY, now=0.0)
    assert state.feed_status(now=0.0) == "stale"
    state.apply_publish(CONTEXT, EMPTY, now=0.0)
    assert state.feed_status(now=9.99) == "live"
    assert state.feed_status(now=10.0) == "stale"
    state.producer_disconnected()
    assert state.feed_status(now=10.0) == "down"


def test_health_payload_reports_feed_and_retained_snapshot() -> None:
    state = RelayState(clock=lambda: 100.0)
    assert state.health() == {"feed": "down", "producer_count": 0, "has_snapshot": False, "seq": None}

    state.producer_connected()
    state.apply_select(CONTEXT, EMPTY, now=100.0)
    state.apply_publish(CONTEXT, EMPTY, now=100.0)

    assert state.health() == {"feed": "live", "producer_count": 1, "has_snapshot": True, "seq": 2}
