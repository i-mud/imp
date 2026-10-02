from __future__ import annotations

import pytest
from imp_relay.protocol import Character, Vital

from imp_adapter.normalize import Normalizer
from imp_adapter.publisher import RelayPublisher, state_to_wire
from imp_adapter.records import Record, parse_record


def test_record_parser_accepts_a_valid_gmcp_record() -> None:
    result = parse_record('{"at":1234,"package":"Char.Status","payload":{"character_name":"Ariadne"}}')

    assert result.ok
    assert result.record == Record(
        at=1234,
        package="Char.Status",
        payload={"character_name": "Ariadne"},
    )


def test_normalizer_and_wire_projection_are_client_neutral() -> None:
    normalizer = Normalizer()

    normalizer.apply(
        Record(
            at=1,
            package="Char.Status",
            payload={"character_name": "Ariadne"},
        )
    )
    state = normalizer.apply(
        Record(
            at=2,
            package="Char.Vitals",
            payload={"hp": "12", "maxhp": "20"},
        )
    )

    assert state.character == Character(
        name="Ariadne",
        hp=Vital(current=12, max=20),
        mana=None,
        moves=None,
    )
    assert state_to_wire(state) == {
        "character": {
            "name": "Ariadne",
            "hp": {"current": 12, "max": 20},
            "mana": None,
            "moves": None,
        },
        "target": None,
    }


def test_publisher_still_refuses_non_loopback_ingest() -> None:
    with pytest.raises(ValueError, match="loopback"):
        RelayPublisher(url="ws://203.0.113.7:8787/ingest")
