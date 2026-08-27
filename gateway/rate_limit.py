"""按 key（app_id / open_id）隔离的令牌桶限流，Phase 1 进程内实现。

Phase 2 可改为 Redis 分布式实现。
"""
import threading
import time

from shared.errors import RateLimitExceededError


class _KeyState:
    """单个 key 的桶状态。"""
    __slots__ = ("tokens", "last_refill")

    def __init__(self, capacity: float):
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()


class TokenBucket:
    """线程安全的令牌桶，按 key 隔离。"""

    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._buckets: dict[str, _KeyState] = {}
        self._lock = threading.Lock()

    def _refill(self, state: _KeyState) -> None:
        """按时间流逝补充 token，上限为 capacity。"""
        now = time.monotonic()
        elapsed = now - state.last_refill
        if elapsed > 0:
            state.tokens = min(
                self.capacity,
                state.tokens + elapsed * self.refill_per_sec,
            )
            state.last_refill = now

    def acquire(self, key: str, cost: float = 1.0) -> None:
        """获取 token；不足时抛 RateLimitExceededError。"""
        with self._lock:
            state = self._buckets.get(key)
            if state is None:
                state = _KeyState(self.capacity)
                self._buckets[key] = state
            self._refill(state)
            if state.tokens < cost:
                raise RateLimitExceededError(
                    f"rate limit exceeded key={key} tokens={state.tokens:.2f}"
                )
            state.tokens -= cost
