"""令牌桶限流测试。"""
import time

import pytest

from gateway.rate_limit import TokenBucket
from shared.errors import RateLimitExceededError


def test_initial_burst_allowed():
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        bucket.acquire(key="app1")  # 不应抛


def test_sixth_call_in_burst_rejected():
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        bucket.acquire(key="app1")
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="app1")


def test_separate_keys_have_separate_buckets():
    bucket = TokenBucket(capacity=2, refill_per_sec=0.001)
    bucket.acquire(key="a")
    bucket.acquire(key="a")
    bucket.acquire(key="b")
    bucket.acquire(key="b")
    # a 已耗尽
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="a")
    # b 也已耗尽
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="b")


def test_refill_after_wait():
    bucket = TokenBucket(capacity=1, refill_per_sec=100.0)
    bucket.acquire(key="a")
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="a")
    time.sleep(0.05)  # 50ms × 100 tokens/s = 5 tokens 补充
    bucket.acquire(key="a")  # 应放行


def test_custom_cost():
    bucket = TokenBucket(capacity=10, refill_per_sec=0.001)
    bucket.acquire(key="a", cost=7)
    bucket.acquire(key="a", cost=3)
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="a", cost=1)


def test_exception_includes_key_and_tokens():
    bucket = TokenBucket(capacity=1, refill_per_sec=0.001)
    bucket.acquire(key="my_app")
    with pytest.raises(RateLimitExceededError) as exc:
        bucket.acquire(key="my_app")
    msg = str(exc.value).lower()
    assert "my_app" in msg
    assert "tokens" in msg