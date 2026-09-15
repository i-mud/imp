"""Transport-independent retained state and upstream feed liveness."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from .protocol import FeedStatus, GameState


@dataclass(frozen=True)
class Snapshot:
    seq: int
    at: int
    state: GameState


class HealthPayload(TypedDict):
    feed: FeedStatus
    producer_count: int
    has_snapshot: bool
    seq: int | None


class RelayState:
    """Retains the last valid snapshot independently of producer sockets."""

    def __init__(self, stale_after: float = 10.0, clock: Callable[[], float] = time.time) -> None:
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        self._stale_after = stale_after
        self._clock = clock
        self._producer_count = 0
        self._last_publish_at: float | None = None
        self._snapshot: Snapshot | None = None
        self._seq = 0

    @property
    def stale_after(self) -> float:
        return self._stale_after

    def apply_publish(self, state: GameState, now: float) -> Snapshot:
        self._seq += 1
        snapshot = Snapshot(seq=self._seq, at=int(now * 1000), state=state)
        self._snapshot = snapshot
        self._last_publish_at = now
        return snapshot

    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def feed_status(self, now: float) -> FeedStatus:
        if self._producer_count == 0:
            return "down"
        if self._last_publish_at is None or now - self._last_publish_at >= self._stale_after:
            return "stale"
        return "live"

    def producer_connected(self) -> None:
        self._producer_count += 1

    def producer_disconnected(self) -> None:
        if self._producer_count == 0:
            raise RuntimeError("producer count cannot become negative")
        self._producer_count -= 1

    def health(self) -> HealthPayload:
        snapshot = self._snapshot
        return {
            "feed": self.feed_status(self._clock()),
            "producer_count": self._producer_count,
            "has_snapshot": snapshot is not None,
            "seq": snapshot.seq if snapshot is not None else None,
        }
