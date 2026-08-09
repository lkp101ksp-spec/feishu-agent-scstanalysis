import time

from orchestrator.tools.bio.rate_limiter import RateLimiter


def test_rate_limiter_allows_first_call_immediately():
    rl = RateLimiter(rate=3, per_sec=1.0)
    t0 = time.monotonic()
    rl.wait()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.01


def test_rate_limiter_enforces_interval():
    rl = RateLimiter(rate=3, per_sec=1.0)
    rl.wait()
    t0 = time.monotonic()
    rl.wait()  # 第二次调用应等待 ~0.333s
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.30  # 留出一些余量