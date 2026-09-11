"""startup_wait 启动期依赖等待单测（probe/sleep/clock 注入，纯逻辑）。

行为约定（2026-09-11 Docker 缺席连崩 189 次事故驱动）：
- 首轮就绪 → 直接返回 0，无 sleep
- 失败 → 每 interval 重试，探针抛异常按未就绪处理
- 等待超过 cap → SystemExit（非零退出码让 guardian 重新拉起）
- 等待期间日志节流（log_every_sec），恢复时记耗时
"""
from __future__ import annotations

import pytest

from gateway.startup_wait import wait_for_dependency


class _Clock:
    """手动推进的假时钟；sleep 累计即时间流逝。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, sec: float) -> None:
        self.sleeps.append(sec)
        self.now += sec


def _flaky_probe(fail_times: int):
    """前 fail_times 次返回 False，之后 True。"""
    state = {"calls": 0}

    def probe() -> bool:
        state["calls"] += 1
        return state["calls"] > fail_times

    return probe, state


def test_ready_immediately_returns_zero():
    clock = _Clock()
    probe, state = _flaky_probe(0)
    waited = wait_for_dependency(
        probe, interval_sec=5, cap_sec=1800,
        clock=clock.monotonic, sleep=clock.sleep, name="pg")
    assert waited == 0.0
    assert state["calls"] == 1
    assert clock.sleeps == []


def test_ready_after_retries():
    clock = _Clock()
    probe, state = _flaky_probe(3)
    waited = wait_for_dependency(
        probe, interval_sec=5, cap_sec=1800,
        clock=clock.monotonic, sleep=clock.sleep, name="pg")
    assert state["calls"] == 4
    assert clock.sleeps == [5, 5, 5]
    assert waited == 15.0


def test_probe_exception_treated_as_not_ready():
    clock = _Clock()
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("pg down")
        return True

    waited = wait_for_dependency(
        probe, interval_sec=5, cap_sec=1800,
        clock=clock.monotonic, sleep=clock.sleep, name="pg")
    assert calls["n"] == 2
    assert waited == 5.0


def test_cap_exceeded_raises_system_exit():
    clock = _Clock()
    probe, _ = _flaky_probe(10**9)  # 永不就绪
    with pytest.raises(SystemExit):
        wait_for_dependency(
            probe, interval_sec=5, cap_sec=20,
            clock=clock.monotonic, sleep=clock.sleep, name="pg")
    # 第 20s 睡眠后总等待达到 cap → 退出；不应多探
    assert sum(clock.sleeps) >= 20


def test_cap_boundary_last_probe_succeeds():
    """等待恰好到 cap 前最后一探就绪 → 正常返回不退出。"""
    clock = _Clock()
    # interval=5, cap=20：失败 3 次（等待 15s）后第 4 探就绪
    probe, state = _flaky_probe(3)
    waited = wait_for_dependency(
        probe, interval_sec=5, cap_sec=20,
        clock=clock.monotonic, sleep=clock.sleep, name="pg")
    assert waited == 15.0
    assert state["calls"] == 4


def test_wait_for_pg_skips_sqlite():
    """sqlite（本地开发/测试）无 pg 依赖，直通不等待。"""
    from types import SimpleNamespace

    from gateway.ws_client import wait_for_pg_ready

    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        return True

    settings = SimpleNamespace(database_url="sqlite:///x.db")
    wait_for_pg_ready(settings, probe=probe)
    assert calls["n"] == 0


def test_wait_for_pg_probes_postgres():
    """pg URL 走探针等待（探针注入，单测不打真实连接）。"""
    from types import SimpleNamespace

    from gateway.ws_client import wait_for_pg_ready

    calls = {"n": 0}

    def probe(url: str) -> bool:
        calls["n"] += 1
        assert url == "postgresql+psycopg://u:p@h/db"
        return True

    settings = SimpleNamespace(
        database_url="postgresql+psycopg://u:p@h/db")
    wait_for_pg_ready(settings, probe=probe)
    assert calls["n"] == 1
