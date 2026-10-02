from __future__ import annotations

from pathlib import Path

from imp_adapter.records import parse_record

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_sanitized_real_session_records_parse() -> None:
    results = [parse_record(line) for line in (FIXTURES / "real-session.jsonl").read_text().splitlines()]

    assert all(result.ok for result in results)
    assert [result.record.package for result in results if result.record is not None] == [
        "Char.Group.List",
        "Char.Status",
        "Char.Vitals",
        "Char.Vitals",
        "Char.Vitals",
        "Char.Status",
        "Char.Status",
        "Char.Vitals",
        "Char.Vitals",
        "Char.Status",
        "Char.Status",
        "Char.Status",
        "Char.Vitals",
        "Char.Status",
    ]


def test_every_hostile_fixture_line_is_rejected() -> None:
    results = [parse_record(line) for line in (FIXTURES / "malformed.jsonl").read_text().splitlines()]

    assert len(results) == 6
    assert all(not result.ok for result in results)
    assert {result.error for result in results} == {
        "invalid_json",
        "record_not_object",
        "invalid_at",
        "invalid_package",
        "invalid_payload",
        "record_too_large",
    }
