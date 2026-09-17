"""Conservative, side-effect-free normalization from GMCP to TinyScry state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tinyscry_relay.protocol import Character, GameState, Target, Vital

from tinyscry_tf.records import JsonValue, Record

MAX_VITAL: Final = 1_000_000_000
MAX_NAME_CHARS: Final = 64
_INTEGER_TEXT: Final = re.compile(r"[+-]?\d+\Z")
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True)
class VitalKeys:
    current: tuple[str, ...]
    maximum: tuple[str, ...]


@dataclass(frozen=True)
class TargetMapping:
    package: str
    name: tuple[str, ...]
    health_percent: tuple[str, ...]


# Mappings below contain only package and field names observed in the redacted
# real-session fixture.
VITAL_MAPPINGS: Final = {
    "Char.Vitals": {
        "hp": VitalKeys(current=("hp",), maximum=("maxhp",)),
        "mana": VitalKeys(current=("mp",), maximum=("maxmp",)),
        "moves": VitalKeys(current=("mv",), maximum=("maxmv",)),
    },
    "Char.Status": {
        "hp": VitalKeys(current=("health",), maximum=("health_max",)),
        "mana": VitalKeys(current=("mana",), maximum=("mana_max",)),
        "moves": VitalKeys(current=("movement",), maximum=("movement_max",)),
    },
}
NAME_MAPPINGS: Final = {"Char.Status": ("character_name",)}
TARGET_MAPPINGS: Final = (
    TargetMapping(
        package="Char.Status",
        name=("opponent_name",),
        health_percent=("opponent_health",),
    ),
)
# AVATAR does not always emit an explicit opponent-clear record. The redacted
# fixture contains one (empty opponent_name), but a later capture ended a fight
# with no clear record at all, so both mechanisms are required: the explicit
# empty opponent_name above, and this observed combat-position lifecycle.
POSITION_MAPPINGS: Final = {"Char.Vitals": ("position",)}
FIGHT_POSITION: Final = "Fight"


def _is_unsafe_text_character(character: str) -> bool:
    code_point = ord(character)
    return code_point <= 0x1F or 0x7F <= code_point <= 0x9F or 0xD800 <= code_point <= 0xDFFF


def strip_control_characters(value: str) -> str:
    """Produce a protocol-safe display label from untrusted GMCP text."""

    safe_characters: list[str] = []
    utf16_units = 0
    for character in value:
        if _is_unsafe_text_character(character):
            continue
        character_units = 2 if ord(character) > 0xFFFF else 1
        if utf16_units + character_units > MAX_NAME_CHARS:
            break
        safe_characters.append(character)
        utf16_units += character_units
    return "".join(safe_characters)


def _first_value(payload: JsonObject, keys: tuple[str, ...]) -> JsonValue | None:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _integer(value: JsonValue | None) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value):
        return int(value)
    return None


def _clamp_vital(value: int) -> int:
    return max(0, min(value, MAX_VITAL))


def _update_vital(previous: Vital | None, payload: JsonObject, keys: VitalKeys) -> Vital | None:
    current = _integer(_first_value(payload, keys.current))
    maximum = _integer(_first_value(payload, keys.maximum))
    if current is None and maximum is None:
        return previous
    if previous is None:
        if current is None or maximum is None:
            return None
        return Vital(current=_clamp_vital(current), max=_clamp_vital(maximum))
    return Vital(
        current=_clamp_vital(current if current is not None else previous.current),
        max=_clamp_vital(maximum if maximum is not None else previous.max),
    )


def _read_percent(value: JsonValue | None) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        percent = float(value)
    elif isinstance(value, str):
        text = value.removesuffix("%").strip()
        try:
            percent = float(text)
        except ValueError:
            return None
    else:
        return None
    if percent != percent or percent == float("inf") or percent == float("-inf"):
        return None
    return max(0.0, min(percent, 100.0))


def _with_target(state: GameState, payload: JsonValue, mapping: TargetMapping) -> GameState:
    if not isinstance(payload, dict):
        return state

    raw_name = _first_value(payload, mapping.name)
    if raw_name is None:
        if state.target is None:
            return state
        name = state.target.name
    elif isinstance(raw_name, str):
        name = strip_control_characters(raw_name)
        if not name:
            candidate = GameState(character=state.character, target=None)
            return state if candidate == state else candidate
    else:
        return state

    raw_health = _first_value(payload, mapping.health_percent)
    health_percent = _read_percent(raw_health)
    if raw_health is None and state.target is not None and name == state.target.name:
        health_percent = state.target.health_percent
    candidate = GameState(
        character=state.character,
        target=Target(name=name, health_percent=health_percent),
    )
    return state if candidate == state else candidate


class Normalizer:
    """Accumulates independently delivered GMCP packages into complete state.

    This is the only normalization path. Identity, resources and target come in
    separate packages, and sometimes in one, so state is accumulated per field
    rather than derived from a single record. Unrecognized packages are
    deliberately identity-preserving: server-only GMCP traffic cannot perturb
    the HUD.

    A target restored from the private checkpoint is supplied as
    ``seeded_target``. It carries no observed combat history, so the first
    non-Fight position retires it instead of leaving it to survive until some
    future fight ends. Only the feed knows that provenance; it is never
    inferred from the target's contents.
    """

    def __init__(self, *, seeded_target: Target | None = None) -> None:
        self._state = GameState(character=None, target=seeded_target)
        self._name: str | None = None
        self._hp: Vital | None = None
        self._mana: Vital | None = None
        self._moves: Vital | None = None
        self._target: Target | None = seeded_target
        self._position: str | None = None
        self._target_is_seeded = seeded_target is not None

    @property
    def state(self) -> GameState:
        return self._state

    def _rebuild_character(self) -> GameState:
        character = (
            None
            if self._name is None
            else Character(name=self._name, hp=self._hp, mana=self._mana, moves=self._moves)
        )
        return GameState(character=character, target=self._target)

    def apply(self, record: Record) -> GameState:
        if isinstance(record.payload, dict):
            vital_mapping = VITAL_MAPPINGS.get(record.package)
            if vital_mapping is not None:
                self._hp = _update_vital(self._hp, record.payload, vital_mapping["hp"])
                self._mana = _update_vital(self._mana, record.payload, vital_mapping["mana"])
                self._moves = _update_vital(self._moves, record.payload, vital_mapping["moves"])

            name_mapping = NAME_MAPPINGS.get(record.package)
            if name_mapping is not None:
                raw_name = _first_value(record.payload, name_mapping)
                if isinstance(raw_name, str):
                    name = strip_control_characters(raw_name)
                    if name:
                        self._name = name

            position_mapping = POSITION_MAPPINGS.get(record.package)
            if position_mapping is not None:
                raw_position = _first_value(record.payload, position_mapping)
                if isinstance(raw_position, str):
                    position = strip_control_characters(raw_position).strip()
                    if position:
                        # Either the target was only restored from a checkpoint
                        # and live play is not in combat, or combat just ended.
                        if position != FIGHT_POSITION and (
                            self._target_is_seeded or self._position == FIGHT_POSITION
                        ):
                            self._target = None
                        self._target_is_seeded = False
                        self._position = position

        state = self._rebuild_character()
        for mapping in TARGET_MAPPINGS:
            if record.package == mapping.package:
                updated = _with_target(state, record.payload, mapping)
                self._target = updated.target
                if isinstance(record.payload, dict) and isinstance(
                    _first_value(record.payload, mapping.name), str
                ):
                    # A live opponent_name is authoritative. Health-only deltas
                    # must never confirm a target restored from the checkpoint.
                    self._target_is_seeded = False
                break
        self._state = self._rebuild_character()
        return self._state
