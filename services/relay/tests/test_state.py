from __future__ import annotations

import pytest

from imp_relay.protocol import Character, GameState, StateContext
from imp_relay.state import RelayState

EMPTY = GameState(character=None, target=None)
CONTEXT = StateContext("session1", 1, 1)


@pytest.mark.parametrize("stale_after", [float("inf"), float("-inf"), float("nan"), 0.0, -1.0])
def test_relay_state_rejects_nonpositive_or_nonfinite_stale_after(stale_after: float) -> None:
    with pytest.raises(ValueError):
        RelayState(stale_after=stale_after)


def test_sequence_tracks_emitted_snapshots_and_disconnect_retains_snapshot() -> None:
    state = RelayState(clock=lambda: 10.0)
    state.producer_connected()
    selected = state.apply_select(CONTEXT, EMPTY, now=2.0)
    changed = state.apply_publish(
        CONTEXT, GameState(character=Character("Ada", None, None, None), target=None), now=3.0
    )
    assert changed is not None
    state.producer_disconnected()

    assert (selected.seq, changed.seq) == (1, 2)
    assert state.snapshot() == changed
    assert state.feed_status(now=3.0) == "down"


def test_identical_observations_refresh_freshness_without_replacing_snapshot() -> None:
    state = RelayState(stale_after=10.0)
    state.producer_connected()
    selected = state.apply_select(CONTEXT, EMPTY, now=0.0)
    assert (selected.seq, selected.at) == (1, 0)

    for now in (0.0, 9.0, 18.0, 27.0):
        assert state.apply_publish(CONTEXT, EMPTY, now=now) is None
        assert state.snapshot() is selected
        assert state.feed_status(now=now + 9.99) == "live"
        assert state.feed_status(now=now + 10.0) == "stale"

    other = StateContext("session1", 2, 1)
    assert state.apply_publish(other, EMPTY, now=40.0) is None
    assert state.feed_status(now=40.0) == "stale"
    assert state.apply_publish(CONTEXT, EMPTY, now=41.0) is None
    assert state.snapshot() == selected
    assert state.feed_status(now=50.99) == "live"
    assert state.feed_status(now=51.0) == "stale"

    reselected = state.apply_select(CONTEXT, EMPTY, now=52.0)
    assert reselected.seq == 2
    assert state.feed_status(now=52.0) == "stale"
    state.producer_disconnected()


@pytest.mark.parametrize(
    "other",
    [StateContext("other", 1, 1), StateContext("session1", 2, 1), StateContext("session1", 1, 2)],
)
def test_publish_only_updates_the_selected_context(other: StateContext) -> None:
    state = RelayState()
    state.producer_connected()
    selected = state.apply_select(CONTEXT, EMPTY, now=1.0)

    assert state.apply_publish(other, EMPTY, now=2.0) is None
    assert state.snapshot() == selected
    assert state.feed_status(now=2.0) == "stale"


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

    assert state.health() == {"feed": "live", "producer_count": 1, "has_snapshot": True, "seq": 1}
