from __future__ import annotations

from tinyscry_relay.protocol import Character, GameState, Target, Vital

from tinyscry_tf.normalize import Normalizer, normalize_record
from tinyscry_tf.records import JsonValue, Record


def _record(package: str, payload: JsonValue) -> Record:
    return Record(at=1_710_000_000_000, package=package, payload=payload)


def test_vitals_accept_strings_and_clamp_protocol_bounds() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Name", {"name": "Ariadne"}))

    state = normalizer.apply(
        _record(
            "Char.Vitals",
            {
                "hp": "-5",
                "maxhp": "1000000009",
                "mana": "14",
                "maxmana": "20",
                "moves": "34",
                "maxmoves": "40",
            },
        )
    )

    assert state.character == Character(
        name="Ariadne",
        hp=Vital(current=0, max=1_000_000_000),
        mana=Vital(current=14, max=20),
        moves=Vital(current=34, max=40),
    )


def test_vitals_buffered_before_identity() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Vitals", {"hp": "12", "maxhp": "20"}))

    state = normalizer.apply(_record("Char.Base", {"name": "Ariadne"}))

    assert state.character == Character(name="Ariadne", hp=Vital(current=12, max=20), mana=None, moves=None)


def test_control_characters_are_removed_from_names() -> None:
    state = Normalizer().apply(_record("Char.Name", {"name": "Ari\u0007adne\u009b"}))

    assert state.character == Character(name="Ariadne", hp=None, mana=None, moves=None)


def test_unknown_package_preserves_exact_previous_state() -> None:
    state = GameState(character=Character(name="Ariadne", hp=None, mana=None, moves=None), target=None)

    normalized = normalize_record(state, _record("Room.Info", {"num": 1}))

    assert normalized is state


def test_target_acquisition_damage_and_clearing() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Name", {"name": "Ariadne"}))

    acquired = normalizer.apply(_record("IRE.Target.Info", {"short_desc": "a cave troll", "hpperc": "100%"}))
    damaged = normalizer.apply(_record("IRE.Target.Info", {"short_desc": "a cave troll", "hpperc": 42}))
    cleared = normalizer.apply(_record("IRE.Target.Info", None))

    assert acquired.target == Target(name="a cave troll", health_percent=100.0)
    assert damaged.target == Target(name="a cave troll", health_percent=42.0)
    assert cleared.target is None
