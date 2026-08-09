"""简易 token bucket rate limiter。"""
from __future__ import annotations

import time


class RateLimiter:
    """3 req/s token bucket。"""

    def __init__(self, rate: float = 3.0, per_sec: float = 1.0) -> None:
        self.rate = rate
        self.per_sec = per_sec
        self.interval = per_sec / rate
        self._last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.monotonic()