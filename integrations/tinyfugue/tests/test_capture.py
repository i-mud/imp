from __future__ import annotations

from io import StringIO

from tinyscry_tf.capture import convert_lines, encode_record, parse_raw_gmcp
from tinyscry_tf.records import MAX_RECORD_CHARS


def test_actual_tinyfugue_line_becomes_adapter_record() -> None:
    parsed = parse_raw_gmcp('1789502552.922742 Core.Test {"value":"text; slash-command remains data"}\n')

    assert parsed.ok
    assert parsed.record is not None
    assert parsed.record.at == 1789502552922
    assert parsed.record.package == "Core.Test"
    assert parsed.record.payload == {"value": "text; slash-command remains data"}
    assert encode_record(parsed) == (
        '{"at":1789502552922,"package":"Core.Test","payload":{"value":"text; slash-command remains data"}}'
    )


def test_package_without_payload_becomes_json_null() -> None:
    parsed = parse_raw_gmcp("1789502552.922742 Core.Ping\n")

    assert parsed.ok
    assert parsed.record is not None
    assert parsed.record.payload is None


def test_malformed_raw_records_are_rejected_without_stopping_conversion() -> None:
    oversized = f"1 Core.Test {('x' * MAX_RECORD_CHARS)}"
    lines = [
        "not-a-time Core.Test {}\n",
        "1 Core.Test {broken}\n",
        "1 Bad\x07 {}\n",
        oversized,
        '2 Char.Vitals {"hp":"9"}\n',
    ]
    output = StringIO()

    stats = convert_lines(lines, output)

    assert stats.received == 5
    assert stats.rejected == 4
    assert stats.written == 1
    assert output.getvalue() == '{"at":2000,"package":"Char.Vitals","payload":{"hp":"9"}}\n'
