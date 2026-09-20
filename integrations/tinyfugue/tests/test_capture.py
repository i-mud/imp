from __future__ import annotations

from io import StringIO

from tinyscry_tf.capture import convert_lines, encode_record, parse_raw_gmcp
from tinyscry_tf.events import GmcpEvent, ResetEvent, SelectEvent, decode_tf_token, parse_tf_event
from tinyscry_tf.records import MAX_RECORD_CHARS


def test_versioned_gmcp_event_becomes_adapter_record() -> None:
    parsed = parse_raw_gmcp(
        "TS2 G 123_46_45 7 Avatar_32_World 1789502552.922742 "
        'Core.Test {"value":"text; slash-command remains data"}\n'
    )

    assert parsed.ok
    assert parsed.record is not None
    assert parsed.record.at == 1789502552922
    assert parsed.record.package == "Core.Test"
    assert parsed.record.payload == {"value": "text; slash-command remains data"}
    assert encode_record(parsed) == (
        '{"at":1789502552922,"package":"Core.Test","payload":{"value":"text; slash-command remains data"}}'
    )


def test_event_parser_distinguishes_gmcp_reset_selection_and_no_world() -> None:
    gmcp = parse_tf_event("TS2 G s1 3 Avatar 1 Char.Ping")
    reset = parse_tf_event("TS2 R s1 4 Avatar 2")
    selected = parse_tf_event("TS2 S s1 8 4 Avatar 3")
    no_world = parse_tf_event("TS2 S s1 9 0 - 4")

    assert isinstance(gmcp.event, GmcpEvent)
    assert isinstance(reset.event, ResetEvent)
    assert isinstance(selected.event, SelectEvent) and selected.event.context is not None
    assert isinstance(no_world.event, SelectEvent) and no_world.event.context is None


def test_textencode_tokens_decode_strictly() -> None:
    assert decode_tf_token("Avatar_32_World_95_2") == "Avatar World_2"
    assert decode_tf_token("bad_under_score") is None
    assert decode_tf_token("_10_") is None


def test_malformed_events_are_rejected_without_stopping_conversion() -> None:
    oversized = f"TS2 G s1 1 Avatar 1 Core.Test {('x' * (MAX_RECORD_CHARS + 600))}"
    lines = [
        "1700000000 Char.Status {}\n",
        "TS3 G s1 1 Avatar 1 Core.Test {}\n",
        "TS2 G bad/session 1 Avatar 1 Core.Test {}\n",
        "TS2 G s1 1 Avatar 1 Core.Test {broken}\n",
        oversized,
        'TS2 G s1 1 Avatar 2 Char.Vitals {"hp":"9"}\n',
    ]
    output = StringIO()

    stats = convert_lines(lines, output)

    assert stats.received == 6
    assert stats.rejected == 5
    assert stats.written == 1
    assert output.getvalue() == '{"at":2000,"package":"Char.Vitals","payload":{"hp":"9"}}\n'
