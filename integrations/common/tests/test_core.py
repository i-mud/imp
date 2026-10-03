from __future__ import annotations

import pytest
from imp_relay.protocol import Character, Target, Vital

from imp_adapter.normalize import Normalizer
from imp_adapter.publisher import RelayPublisher, state_to_wire
from imp_adapter.records import JsonValue, Record, parse_record


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


@pytest.mark.parametrize("opening,closing", [("[", "]"), ('{"child":', "}")])
@pytest.mark.parametrize("depth,accepted", [(32, True), (33, False), (1100, False)])
def test_record_payload_nesting_is_bounded(opening: str, closing: str, depth: int, accepted: bool) -> None:
    payload = opening * depth + "0" + closing * depth
    result = parse_record('{"at":1,"package":"Room.Info","payload":' + payload + "}")

    assert result.ok is accepted
    if not accepted:
        assert result.record is None
        assert result.error in {"invalid_payload", "invalid_json"}


@pytest.mark.parametrize(
    "invalid",
    [
        {"health": "5", "mana": "9" * 5000},
        {"health": "5", "mana": 10**399},
        {"health": "5", "mana": float("inf")},
        {"health": "5", "mana": 1_000_000_001},
        {"health": "5", "mana": -1},
        {"health": "5", "mana": "not a number"},
        {"health": "5", "mana": True},
        {"character_name": "Rejected", "health": "5", "opponent_health": 10**399},
        {"character_name": "Rejected", "health": "5", "opponent_health": "9" * 5000},
        {"character_name": "Rejected", "health": "5", "opponent_health": "1e400"},
        {"health": "5", "opponent_health": float("inf")},
        {"health": "5", "opponent_health": float("-inf")},
        {"health": "5", "opponent_health": float("nan")},
        {"health": "5", "opponent_health": 101},
        {"health": "5", "opponent_health": -1},
    ],
)
def test_rejected_record_has_no_immediate_or_later_state_effect(invalid: dict[str, JsonValue]) -> None:
    normalizer = Normalizer()
    previous = normalizer.apply(
        Record(
            at=1,
            package="Char.Status",
            payload={
                "character_name": "Ariadne",
                "health": "90",
                "health_max": "100",
                "mana": "70",
                "mana_max": "80",
                "movement": "50",
                "movement_max": "60",
                "opponent_name": "Troll",
                "opponent_health": "62",
            },
        )
    )

    assert normalizer.apply(Record(at=2, package="Char.Status", payload=invalid)) == previous
    assert normalizer.state == previous
    later = normalizer.apply(Record(at=3, package="Char.Status", payload={"mana": "71"}))

    assert later.character == Character(
        name="Ariadne",
        hp=Vital(90, 100),
        mana=Vital(71, 80),
        moves=Vital(50, 60),
    )
    assert later.target == Target("Troll", 62.0)


def test_rejected_record_cannot_contaminate_vitals_buffered_before_identity() -> None:
    normalizer = Normalizer()
    normalizer.apply(Record(at=1, package="Char.Vitals", payload={"hp": "90", "maxhp": "100"}))
    normalizer.apply(
        Record(at=2, package="Char.Vitals", payload={"hp": "5", "mp": "9" * 5000, "maxmp": "80"})
    )

    later = normalizer.apply(Record(at=3, package="Char.Status", payload={"character_name": "Ariadne"}))

    assert later.character == Character("Ariadne", Vital(90, 100), None, None)


def test_rejected_opponent_record_does_not_confirm_checkpoint_target() -> None:
    seeded = Target("Troll", 62.0)
    normalizer = Normalizer(seeded_target=seeded)
    previous = normalizer.state

    assert (
        normalizer.apply(
            Record(
                at=1,
                package="Char.Status",
                payload={
                    "character_name": "Rejected",
                    "opponent_name": "Rejected",
                    "opponent_health": 10**399,
                },
            )
        )
        == previous
    )
    assert normalizer.apply(Record(at=2, package="Char.Vitals", payload={"position": "Stand"})).target is None


def test_invalid_health_only_delta_rejects_other_updates_even_without_a_target() -> None:
    normalizer = Normalizer()
    previous = normalizer.apply(Record(at=1, package="Char.Status", payload={"character_name": "Ariadne"}))

    assert (
        normalizer.apply(
            Record(
                at=2,
                package="Char.Status",
                payload={"character_name": "Rejected", "opponent_health": 10**399},
            )
        )
        == previous
    )


@pytest.mark.parametrize("percent", [0, 100, " 87.5%"])
def test_numeric_boundaries_and_existing_avatar_syntax_remain_valid(percent: int | str) -> None:
    state = Normalizer().apply(
        Record(
            at=1,
            package="Char.Status",
            payload={
                "character_name": "Ariadne",
                "health": "+0001000000000",
                "health_max": 1_000_000_000,
                "mana": "-0",
                "mana_max": "0",
                "opponent_name": "Troll",
                "opponent_health": percent,
            },
        )
    )

    assert state.character == Character("Ariadne", Vital(1_000_000_000, 1_000_000_000), Vital(0, 0), None)
    assert state.target == Target("Troll", 87.5 if isinstance(percent, str) else percent)


def test_normalizer_observed_tracks_latest_canonical_evidence() -> None:
    normalizer = Normalizer()

    def apply(package: str, payload: JsonValue) -> bool:
        normalizer.apply(Record(at=1, package=package, payload=payload))
        return normalizer.observed

    assert not apply("Room.Info", {"name": "Ignored"})
    assert not apply("Char.Status", {})
    assert not apply("Char.Status", {"character_name": None})
    assert not apply("Char.Status", {"opponent_health": "62"})
    assert not apply("Char.Vitals", {"position": "Fight"})
    assert not apply("Char.Vitals", {"hp": None, "maxhp": None})
    assert not apply("Char.Vitals", {"hp": "12", "maxhp": "20"})

    assert apply("Char.Status", {"character_name": "Ariadne"})
    assert apply("Char.Status", {"character_name": "Ariadne"})
    assert apply("Char.Vitals", {"hp": "12", "maxhp": "20"})
    assert apply("Char.Vitals", {"hp": "12", "maxhp": "20"})
    assert apply("Char.Status", {"opponent_name": "Troll"})
    assert apply("Char.Status", {"opponent_name": "Troll"})
    assert apply("Char.Status", {"opponent_health": "62"})
    assert apply("Char.Status", {"opponent_name": ""})

    assert not apply("Char.Status", {"opponent_name": "Rejected", "opponent_health": 101})
    assert not normalizer.observed
    assert not apply("Room.Info", ["not", "an", "object"])

    assert not apply("Char.Vitals", {"position": "Fight"})
    assert apply("Char.Status", {"opponent_name": "Troll"})
    assert not apply("Char.Vitals", {"position": "Fight"})
    assert apply("Char.Vitals", {"position": "Stand"})
