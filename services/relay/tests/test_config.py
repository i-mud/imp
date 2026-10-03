from __future__ import annotations

import pytest

from imp_relay.config import RelayConfig, parse_args


@pytest.mark.parametrize("stale_after", [float("inf"), float("-inf"), float("nan"), 0.0, -1.0])
def test_relay_config_rejects_nonpositive_or_nonfinite_stale_after(stale_after: float) -> None:
    with pytest.raises(ValueError):
        RelayConfig("127.0.0.1", 8787, stale_after, "INFO")


@pytest.mark.parametrize("duration", ["inf", "-inf", "nan", "0", "-1"])
def test_relay_cli_rejects_nonpositive_or_nonfinite_stale_after(duration: str) -> None:
    with pytest.raises(SystemExit) as rejected:
        parse_args([f"--stale-after={duration}"])
    assert rejected.value.code == 2


def test_relay_cli_accepts_finite_stale_after() -> None:
    assert parse_args(["--stale-after", "2.5"]).stale_after == 2.5
