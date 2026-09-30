from __future__ import annotations

import io
import json
from typing import cast

from imp_mudlet.bridge import run_bridge


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
