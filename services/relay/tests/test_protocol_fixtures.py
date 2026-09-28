from __future__ import annotations

import json
from pathlib import Path

import pytest

from imp_relay.protocol import decode_client_message, decode_server_message

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "protocol" / "fixtures"


def _fixtures(kind: str) -> list[Path]:
    files = sorted((FIXTURES / kind).glob("*.json"))
    assert files, f"fixture corpus is empty: {FIXTURES / kind}"
    return files


@pytest.mark.parametrize("path", _fixtures("accept"), ids=lambda path: path.stem)
def test_accept_fixtures(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    decoder = decode_server_message if fixture["direction"] == "server" else decode_client_message
    result = decoder(fixture["frame"])
    assert fixture["expect"] == "accept"
    assert result.ok, result.error


@pytest.mark.parametrize("path", _fixtures("reject"), ids=lambda path: path.stem)
def test_reject_fixtures(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    decoder = decode_server_message if fixture["direction"] == "server" else decode_client_message
    result = decoder(fixture["frame"])
    assert fixture["expect"] == "reject"
    assert not result.ok
    assert result.error is not None
    assert result.error.code == fixture["code"]
    assert result.error.path == fixture["path"]
