from __future__ import annotations

import asyncio
import io
import json
from typing import cast

import pytest
from imp_relay.protocol import Character, GameState, StateContext, Target, Vital

from imp_mudlet.bridge import run_bridge, run_bridge_async


def test_bridge_reports_exact_generation_transitions() -> None:
    source = io.StringIO(
        "\n".join(
            (
                '{"type":"init","profile":"AVATAR","connected":true,"focused":true}',
                '{"type":"protocol","name":"GMCP","enabled":true}',
                '{"type":"focus","focused":false}',
                '{"type":"focus","focused":true}',
                '{"type":"disconnected"}',
                '{"type":"connected"}',
            )
        )
        + "\n"
    )
    output = io.StringIO()

    run_bridge(source, output, session="mudlet_test")

    frames = [cast(dict[str, object], json.loads(line)) for line in output.getvalue().splitlines()]

    assert frames[0]["context"] == {
        "session": "mudlet_test",
        "foreground": 1,
        "connection": 1,
    }
    assert frames[1]["gmcpEnabled"] is True
    assert frames[2]["context"] is None
    assert frames[3]["context"] == {
        "session": "mudlet_test",
        "foreground": 2,
        "connection": 1,
    }
    assert frames[4]["context"] is None
    assert frames[5]["context"] == {
        "session": "mudlet_test",
        "foreground": 2,
        "connection": 2,
    }


def test_bridge_fails_closed_without_echoing_bad_input() -> None:
    source = io.StringIO('{"type":"init","profile":"bad\\u0007name","connected":false,"focused":false}\n')
    output = io.StringIO()

    run_bridge(source, output, session="mudlet_test")

    assert json.loads(output.getvalue()) == {
        "type": "error",
        "protocol": 1,
        "code": "invalid_init",
    }


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 33 + "0" + "]" * 33,
        "[" * 1100 + "0" + "]" * 1100,
        '{"health":"5","mana":"' + "9" * 5000 + '"}',
        '{"health":"5","opponent_health":' + "9" * 400 + "}",
        '{"health":"5","opponent_health":1e400}',
    ],
)
def test_hostile_gmcp_is_skipped_and_bridge_continues_without_partial_state(payload: str) -> None:
    class Publisher:
        def __init__(self) -> None:
            self.states: list[GameState] = []

        async def select(self, context: StateContext | None, state: GameState) -> None:
            pass

        async def publish(self, context: StateContext, state: GameState) -> None:
            self.states.append(state)

        async def close(self) -> None:
            pass

    async def scenario() -> None:
        source = io.StringIO(
            '{"type":"init","profile":"AVATAR","connected":true,"focused":true}\n'
            '{"type":"gmcp","at":1,"package":"Char.Status","payload":{"character_name":"Ariadne",'
            '"health":"90","health_max":"100","mana":"70","mana_max":"80",'
            '"opponent_name":"Troll","opponent_health":"62"}}\n'
            '{"type":"gmcp","at":2,"package":"Char.Status","payload":' + payload + "}\n"
            '{"type":"gmcp","at":3,"package":"Char.Status","payload":{"character_name":"Continued"}}\n'
        )
        output = io.StringIO()
        publisher = Publisher()

        await asyncio.wait_for(
            run_bridge_async(source, output, session="mudlet_test", publisher_factory=lambda: publisher),
            timeout=1,
        )

        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        assert frames[-1]["type"] == "status"
        assert frames[-1]["context"] == {"session": "mudlet_test", "foreground": 1, "connection": 1}
        assert not any(frame.get("code") == "invalid_lifecycle" for frame in frames)
        assert publisher.states == [
            GameState(Character("Ariadne", Vital(90, 100), Vital(70, 80), None), Target("Troll", 62.0)),
            GameState(Character("Continued", Vital(90, 100), Vital(70, 80), None), Target("Troll", 62.0)),
        ]

    asyncio.run(scenario())
