from __future__ import annotations

from imp_mudlet.protocol import (
    FocusMessage,
    GmcpMessage,
    InitMessage,
    decode_lua_message,
)


def test_init_decodes() -> None:
    decoded = decode_lua_message('{"type":"init","profile":"AVATAR","connected":true,"focused":true}')

    assert decoded.message == InitMessage("AVATAR", True, True)


def test_focus_requires_boolean() -> None:
    decoded = decode_lua_message('{"type":"focus","focused":"yes"}')

    assert not decoded.ok
    assert decoded.error == "invalid_focus"


def test_gmcp_becomes_shared_adapter_record() -> None:
    decoded = decode_lua_message(
        '{"type":"gmcp","at":1234,"package":"Char.Vitals","payload":{"hp":"10","maxhp":"20"}}'
    )

    assert decoded.ok
    assert isinstance(decoded.message, GmcpMessage)
    assert decoded.message.record.at == 1234
    assert decoded.message.record.package == "Char.Vitals"
    assert decoded.message.record.payload == {"hp": "10", "maxhp": "20"}


def test_unknown_message_is_rejected() -> None:
    decoded = decode_lua_message('{"type":"wat"}')

    assert not decoded.ok
    assert decoded.error == "unknown_type"


def test_control_characters_in_profile_are_rejected() -> None:
    decoded = decode_lua_message(
        '{"type":"init","profile":"bad\\u0007name","connected":false,"focused":false}'
    )

    assert not decoded.ok
    assert decoded.error == "invalid_init"


def test_focus_decodes() -> None:
    assert decode_lua_message('{"type":"focus","focused":false}').message == FocusMessage(False)
