from __future__ import annotations

import asyncio
import json
from pathlib import Path

from imp_relay.protocol import GameState, StateContext, decode_game_state

from imp_tf.replay import replay_lines

FIXTURES = Path(__file__).parents[1] / "fixtures"


class _CollectingPublisher:
    def __init__(self) -> None:
        self.selections: list[tuple[StateContext | None, GameState]] = []

    async def select(self, context: None, state: GameState) -> None:
        self.selections.append((context, state))


def test_real_session_dry_run_produces_observed_state_transitions() -> None:
    output: list[str] = []
    with (FIXTURES / "real-session.jsonl").open(encoding="utf-8") as stream:
        stats = asyncio.run(replay_lines(stream, interval=0, publisher=None, write_state=output.append))

    states = [json.loads(line) for line in output]
    assert stats.records == 14
    assert stats.rejected == 0
    assert stats.states == len(states) == 12
    assert all(decode_game_state(state).ok for state in states)
    assert states[4]["target"] == {"name": "Redacted Target", "healthPercent": 81.0}
    assert states[7]["target"] == {"name": "Redacted Target", "healthPercent": 29.0}
    assert states[9]["target"] is None
    assert states[-1] == {
        "character": {
            "name": "Redacted Player",
            "hp": {"current": 1384, "max": 3158},
            "mana": {"current": 2481, "max": 3284},
            "moves": {"current": 2147, "max": 2097},
        },
        "target": None,
    }


def test_dry_run_malformed_fixture_counts_rejections_without_states() -> None:
    output: list[str] = []
    with (FIXTURES / "malformed.jsonl").open(encoding="utf-8") as stream:
        stats = asyncio.run(replay_lines(stream, interval=0, publisher=None, write_state=output.append))

    assert stats.rejected == 6
    assert stats.states == 0
    assert output == []


def test_network_replay_selects_only_non_actionable_null_context() -> None:
    async def scenario() -> None:
        publisher = _CollectingPublisher()
        with (FIXTURES / "real-session.jsonl").open(encoding="utf-8") as stream:
            stats = await replay_lines(stream, interval=0, publisher=publisher, write_state=lambda _: None)

        assert stats.states == len(publisher.selections)
        assert publisher.selections
        assert all(context is None for context, _ in publisher.selections)

    asyncio.run(scenario())
