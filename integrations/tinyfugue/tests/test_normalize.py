from __future__ import annotations

from imp_relay.protocol import Character, Target, Vital

from imp_tf.normalize import Normalizer
from imp_tf.records import JsonValue, Record


def _record(package: str, payload: JsonValue) -> Record:
    return Record(at=1_710_000_000_000, package=package, payload=payload)


def test_observed_status_and_vitals_fields_map_to_character() -> None:
    normalizer = Normalizer()
    status = normalizer.apply(
        _record(
            "Char.Status",
            {
                "character_name": "Redacted Player",
                "health": "90",
                "health_max": "100",
                "mana": "70",
                "mana_max": "80",
                "movement": "50",
                "movement_max": "60",
            },
        )
    )
    vitals = normalizer.apply(
        _record(
            "Char.Vitals",
            {"hp": "88", "maxhp": "100", "mp": "68", "maxmp": "80", "mv": "48", "maxmv": "60"},
        )
    )

    assert status.character == Character(
        name="Redacted Player",
        hp=Vital(current=90, max=100),
        mana=Vital(current=70, max=80),
        moves=Vital(current=50, max=60),
    )
    assert vitals.character == Character(
        name="Redacted Player",
        hp=Vital(current=88, max=100),
        mana=Vital(current=68, max=80),
        moves=Vital(current=48, max=60),
    )


def test_partial_status_vitals_update_preserves_prior_maxima() -> None:
    normalizer = Normalizer()
    normalizer.apply(
        _record(
            "Char.Status",
            {
                "character_name": "Redacted Player",
                "health": "90",
                "health_max": "100",
                "mana": "70",
                "mana_max": "80",
                "movement": "50",
                "movement_max": "60",
            },
        )
    )

    state = normalizer.apply(_record("Char.Status", {"health": "95", "mana": "75", "movement": "55"}))

    assert state.character == Character(
        name="Redacted Player",
        hp=Vital(current=95, max=100),
        mana=Vital(current=75, max=80),
        moves=Vital(current=55, max=60),
    )


def test_only_status_name_bootstraps_character_from_buffered_vitals() -> None:
    normalizer = Normalizer()

    state = normalizer.apply(
        _record(
            "Char.Vitals",
            {"hp": "12", "maxhp": "20", "mp": "30", "maxmp": "40", "mv": "50", "maxmv": "60"},
        )
    )
    assert state.character is None

    state = normalizer.apply(_record("Char.StatusVars", {"character_name": "Character Name"}))
    assert state.character is None

    state = normalizer.apply(_record("Char.Status", {"movement": "49"}))
    assert state.character is None

    state = normalizer.apply(
        _record(
            "Room.Players",
            {
                "Redacted Player": {
                    "name": "Redacted Player",
                    "fullname": "(White Aura) Redacted Player is here.",
                    "race": "Human",
                    "spec": "unknown spec",
                }
            },
        )
    )
    assert state.character is None

    state = normalizer.apply(
        _record(
            "Char.Group.List",
            [
                {
                    "leader": True,
                    "name": "Redacted Player",
                    "hp": "12",
                    "maxhp": "20",
                    "mp": "30",
                    "maxmp": "40",
                    "mv": "49",
                    "maxmv": "60",
                }
            ],
        )
    )
    assert state.character is None

    state = normalizer.apply(_record("Char.Status", {"character_name": "Redacted Player"}))

    assert state.character == Character(
        name="Redacted Player",
        hp=Vital(current=12, max=20),
        mana=Vital(current=30, max=40),
        moves=Vital(current=49, max=60),
    )


def test_control_characters_are_removed_from_observed_name() -> None:
    state = Normalizer().apply(_record("Char.Status", {"character_name": "Redacted\u0007 Player\u009b"}))

    assert state.character == Character(name="Redacted Player", hp=None, mana=None, moves=None)


def test_single_record_carrying_identity_resources_and_target_keeps_all_three() -> None:
    state = Normalizer().apply(
        _record(
            "Char.Status",
            {
                "character_name": "Redacted Player",
                "health": "90",
                "health_max": "100",
                "opponent_name": "Redacted Target",
                "opponent_health": "62",
            },
        )
    )

    assert state.character == Character(
        name="Redacted Player",
        hp=Vital(current=90, max=100),
        mana=None,
        moves=None,
    )
    assert state.target == Target(name="Redacted Target", health_percent=62.0)


def test_unknown_observed_package_preserves_exact_previous_state() -> None:
    normalizer = Normalizer()
    previous = normalizer.apply(
        _record("Char.Status", {"character_name": "Redacted Player", "health": "12", "health_max": "20"})
    )

    normalized = normalizer.apply(_record("Char.Group.List", []))

    assert normalized == previous


def test_observed_target_acquisition_damage_and_clearing() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Status", {"character_name": "Redacted Player"}))

    acquired = normalizer.apply(
        _record(
            "Char.Status",
            {
                "opponent_name": "Redacted Target",
                "opponent_health": "81",
                "opponent_health_max": "100",
            },
        )
    )
    damaged = normalizer.apply(_record("Char.Status", {"opponent_health": "29"}))
    cleared = normalizer.apply(
        _record(
            "Char.Status",
            {"opponent_name": "", "opponent_health": "0", "opponent_health_max": "0"},
        )
    )

    assert acquired.target == Target(name="Redacted Target", health_percent=81.0)
    assert damaged.target == Target(name="Redacted Target", health_percent=29.0)
    assert cleared.target is None


def test_observed_opponent_status_establishes_updates_and_replaces_target() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Vitals", {"hp": "10", "maxhp": "10", "position": "Stand"}))

    acquired = normalizer.apply(
        _record("Char.Status", {"opponent_name": "a half-orc patroller", "opponent_health": "87"})
    )
    in_fight = normalizer.apply(_record("Char.Vitals", {"position": "Fight"}))
    damaged = normalizer.apply(_record("Char.Status", {"opponent_health": "11"}))
    replaced = normalizer.apply(
        _record("Char.Status", {"opponent_name": "a porcufish", "opponent_health": "49"})
    )

    assert acquired.target == Target(name="a half-orc patroller", health_percent=87.0)
    assert in_fight.target == Target(name="a half-orc patroller", health_percent=87.0)
    assert damaged.target == Target(name="a half-orc patroller", health_percent=11.0)
    assert replaced.target == Target(name="a porcufish", health_percent=49.0)


def test_leaving_the_observed_fight_position_clears_the_target() -> None:
    # AVATAR does not always emit the explicit clear record, so leaving "Fight"
    # must end combat on its own.
    for exit_position in ("Stand", "Sleep"):
        normalizer = Normalizer()
        normalizer.apply(
            _record(
                "Char.Status",
                {"opponent_name": "a half-orc patroller", "opponent_health": "87"},
            )
        )
        in_fight = normalizer.apply(_record("Char.Vitals", {"position": "Fight"}))
        left = normalizer.apply(_record("Char.Vitals", {"position": exit_position}))

        assert in_fight.target == Target(name="a half-orc patroller", health_percent=87.0)
        assert left.target is None, exit_position


def test_target_survives_non_fight_positions_until_a_fight_transition() -> None:
    normalizer = Normalizer()
    normalizer.apply(_record("Char.Vitals", {"position": "Stand"}))

    acquired = normalizer.apply(
        _record("Char.Status", {"opponent_name": "a half-orc patroller", "opponent_health": "87"})
    )
    reordered = normalizer.apply(_record("Char.Vitals", {"position": "Stand"}))
    in_fight = normalizer.apply(_record("Char.Vitals", {"position": "Fight"}))

    expected = Target(name="a half-orc patroller", health_percent=87.0)
    assert acquired.target == expected
    assert reordered.target == expected, "a non-Fight record alone must not clear a live target"
    assert in_fight.target == expected


def test_checkpoint_seeded_target_is_retired_by_the_first_non_fight_position() -> None:
    seeded = Target(name="a porcufish", health_percent=49.0)
    normalizer = Normalizer(seeded_target=seeded)

    assert normalizer.state.target == seeded
    assert normalizer.apply(_record("Char.Vitals", {"position": "Stand"})).target is None


def test_checkpoint_seeded_target_survives_an_observed_fight_until_it_ends() -> None:
    seeded = Target(name="a porcufish", health_percent=49.0)
    normalizer = Normalizer(seeded_target=seeded)

    in_fight = normalizer.apply(_record("Char.Vitals", {"position": "Fight"}))
    ended = normalizer.apply(_record("Char.Vitals", {"position": "Stand"}))

    assert in_fight.target == seeded
    assert ended.target is None


def test_live_named_opponent_replaces_and_confirms_a_checkpoint_seeded_target() -> None:
    normalizer = Normalizer(seeded_target=Target(name="a porcufish", health_percent=49.0))

    acquired = normalizer.apply(
        _record("Char.Status", {"opponent_name": "a half-orc patroller", "opponent_health": "87"})
    )
    # Live acquisition is authoritative, so seeded-target retirement no longer
    # applies: only a real Fight -> non-Fight transition may clear it.
    still_targeted = normalizer.apply(_record("Char.Vitals", {"position": "Stand"}))

    expected = Target(name="a half-orc patroller", health_percent=87.0)
    assert acquired.target == expected
    assert still_targeted.target == expected


def test_health_only_delta_neither_confirms_nor_recreates_a_target() -> None:
    normalizer = Normalizer(seeded_target=Target(name="a porcufish", health_percent=49.0))

    # A health-only delta must not launder a seeded target into a live one.
    normalizer.apply(_record("Char.Status", {"opponent_health": "40"}))
    retired = normalizer.apply(_record("Char.Vitals", {"position": "Stand"}))
    after_delta = normalizer.apply(_record("Char.Status", {"opponent_health": "22"}))

    assert retired.target is None
    assert after_delta.target is None
