from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tinyscry_relay.protocol import decode_game_state

from tinyscry_tf.replay import replay_lines

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_dry_run_session_produces_only_valid_states() -> None:
    output: list[str] = []
    with (FIXTURES / "session.jsonl").open(encoding="utf-8") as stream:
        stats = asyncio.run(replay_lines(stream, interval=0, publisher=None, write_state=output.append))

    assert stats.rejected == 0
    assert stats.states == len(output) == 6
    assert all(decode_game_state(json.loads(line)).ok for line in output)


def test_dry_run_malformed_fixture_counts_rejections_without_states() -> None:
    output: list[str] = []
    with (FIXTURES / "malformed.jsonl").open(encoding="utf-8") as stream:
        stats = asyncio.run(replay_lines(stream, interval=0, publisher=None, write_state=output.append))

    assert stats.rejected == 6
    assert stats.states == 0
    assert output == []
