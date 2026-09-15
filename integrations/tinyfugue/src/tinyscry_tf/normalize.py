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


# This is the only MUD-facing mapping table. Update it after preserving a real
# GMCP capture in fixtures and extending tests for that capture.
VITAL_MAPPINGS: Final = {
    "hp": VitalKeys(current=("hp",), maximum=("maxhp",)),
    "mana": VitalKeys(current=("mp", "mana"), maximum=("maxmp", "maxmana")),
    "moves": VitalKeys(current=("ep", "moves"), maximum=("maxep", "maxmoves")),
}
TARGET_MAPPINGS: Final = (
    # UNVERIFIED: confirm that this MUD sends IRE.Target.Info and that its
    # short_desc and hpperc fields mean target label and percentage health.
    TargetMapping(package="IRE.Target.Info", name=("short_desc",), health_percent=("hpperc",)),
)


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


def _read_vital(payload: JsonObject, keys: VitalKeys) -> Vital | None:
    current = _integer(_first_value(payload, keys.current))
    maximum = _integer(_first_value(payload, keys.maximum))
    if current is None or maximum is None:
        return None
    return Vital(current=_clamp_vital(current), max=_clamp_vital(maximum))


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


def _with_vitals(state: GameState, payload: JsonObject) -> GameState:
    if state.character is None:
        return state

    character = state.character
    hp = _read_vital(payload, VITAL_MAPPINGS["hp"]) or character.hp
    mana = _read_vital(payload, VITAL_MAPPINGS["mana"]) or character.mana
    moves = _read_vital(payload, VITAL_MAPPINGS["moves"]) or character.moves
    updated = Character(name=character.name, hp=hp, mana=mana, moves=moves)
    candidate = GameState(character=updated, target=state.target)
    return state if candidate == state else candidate


def _with_name(state: GameState, payload: JsonObject) -> GameState:
    raw_name = _first_value(payload, ("name",))
    if not isinstance(raw_name, str):
        return state
    name = strip_control_characters(raw_name)
    if not name:
        return state
    if state.character is None:
        character = Character(name=name, hp=None, mana=None, moves=None)
    else:
        character = Character(
            name=name,
            hp=state.character.hp,
            mana=state.character.mana,
            moves=state.character.moves,
        )
    candidate = GameState(character=character, target=state.target)
    return state if candidate == state else candidate


def _with_target(state: GameState, payload: JsonValue, mapping: TargetMapping) -> GameState:
    if payload is None:
        candidate = GameState(character=state.character, target=None)
        return state if candidate == state else candidate
    if not isinstance(payload, dict):
        return state

    raw_name = _first_value(payload, mapping.name)
    if not isinstance(raw_name, str):
        return state
    name = strip_control_characters(raw_name)
    if not name:
        return state
    health_percent = _read_percent(_first_value(payload, mapping.health_percent))
    candidate = GameState(
        character=state.character,
        target=Target(name=name, health_percent=health_percent),
    )
    return state if candidate == state else candidate


def normalize_record(previous: GameState, record: Record) -> GameState:
    """Return a complete state after one recognized GMCP record.

    Unknown packages are deliberately identity-preserving so adding server-only
    GMCP traffic cannot perturb the HUD.
    """

    if record.package == "Char.Vitals" and isinstance(record.payload, dict):
        return _with_vitals(previous, record.payload)
    if record.package in {"Char.Name", "Char.Base"} and isinstance(record.payload, dict):
        return _with_name(previous, record.payload)
    for mapping in TARGET_MAPPINGS:
        if record.package == mapping.package:
            return _with_target(previous, record.payload, mapping)
    return previous


class Normalizer:
    """Accumulates independently delivered GMCP packages into complete state."""

    def __init__(self) -> None:
        self._state = GameState(character=None, target=None)
        self._name: str | None = None
        self._hp: Vital | None = None
        self._mana: Vital | None = None
        self._moves: Vital | None = None
        self._target: Target | None = None

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
        if record.package == "Char.Vitals" and isinstance(record.payload, dict):
            hp = _read_vital(record.payload, VITAL_MAPPINGS["hp"])
            mana = _read_vital(record.payload, VITAL_MAPPINGS["mana"])
            moves = _read_vital(record.payload, VITAL_MAPPINGS["moves"])
            self._hp = hp or self._hp
            self._mana = mana or self._mana
            self._moves = moves or self._moves
            self._state = self._rebuild_character()
            return self._state

        if record.package in {"Char.Name", "Char.Base"} and isinstance(record.payload, dict):
            raw_name = _first_value(record.payload, ("name",))
            if isinstance(raw_name, str):
                name = strip_control_characters(raw_name)
                if name:
                    self._name = name
                    self._state = self._rebuild_character()
            return self._state

        for mapping in TARGET_MAPPINGS:
            if record.package == mapping.package:
                updated = _with_target(self._rebuild_character(), record.payload, mapping)
                self._target = updated.target
                self._state = self._rebuild_character()
                return self._state
        return self._state
