from __future__ import annotations

from tinyscry_relay.protocol import (
    Character,
    GameState,
    RelayInfo,
    SnapshotMessage,
    Target,
    Vital,
    decode_client_message,
    decode_game_state,
    decode_server_message,
    encode_hello,
    encode_publish,
    encode_snapshot,
    encode_status,
)


def test_absent_nullable_fields_decode_as_null() -> None:
    result = decode_game_state({})

    assert result.ok
    assert result.value == GameState(character=None, target=None)


def test_rejects_unpaired_surrogate_after_json_decode() -> None:
    result = decode_client_message('{"type":"publish","protocol":1,"state":{"character":{"name":"\\ud800"}}}')

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "invalid_field"
    assert result.error.path == "state.character.name"


def test_frame_limit_is_counted_in_utf16_code_units() -> None:
    oversized = "\U0001f600" * 8_193
    result = decode_server_message(oversized)

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "frame_too_large"


def test_json_numbers_with_integral_float_syntax_decode_as_integers() -> None:
    result = decode_server_message(
        '{"type":"snapshot","protocol":1.0,"seq":2e0,"at":3.0,"state":{"character":null,"target":null}}'
    )

    assert result.ok
    assert isinstance(result.value, SnapshotMessage)
    assert result.value.seq == 2
    assert result.value.at == 3


def test_encoders_emit_wire_field_names_and_round_trip() -> None:
    state = GameState(
        character=Character("Ada", Vital(20, 10), None, Vital(4, 5)),
        target=Target("Troll", 87.5),
    )

    publish = decode_client_message(encode_publish(state))
    snapshot = decode_server_message(encode_snapshot(4, 123, state))
    hello = decode_server_message(encode_hello(123, RelayInfo("relay", "1.2")))
    status = decode_server_message(encode_status(123, "live", None))

    assert publish.ok and publish.value is not None and publish.value.state == state
    assert snapshot.ok and isinstance(snapshot.value, SnapshotMessage) and snapshot.value.state == state
    assert hello.ok
    assert status.ok
