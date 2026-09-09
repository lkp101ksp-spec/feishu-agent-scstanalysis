"""DBHealthMonitor 单测：探活状态机 + 指数退避 + 告警只在状态跃迁时发。

2026-09-09 事故驱动：Docker 停运 → pg 不可达 → ws_client 静默死亡无人知晓。
监控逻辑抽成纯类（probe/alert 注入），线程壳只做 sleep 循环。
"""
from gateway.health_monitor import DBHealthMonitor


def _make(probe_results, alerts, **kw):
    """构造 monitor：probe 依次返回 probe_results，alert 记录到 alerts。"""
    it = iter(probe_results)
    mon = DBHealthMonitor(probe=lambda: next(it), alert=alerts.append, **kw)
    return mon


def test_first_tick_healthy_no_alert():
    """首轮健康：零告警（正常启动不打扰）。"""
    alerts = []
    mon = _make([True], alerts)
    assert mon.tick() is True
    assert alerts == []


def test_transition_to_down_alerts_once():
    """健康→故障跃迁：发一条告警；持续故障不重复发。"""
    alerts = []
    mon = _make([True, False, False, False], alerts)
    mon.tick()
    mon.tick()
    mon.tick()
    mon.tick()
    assert len(alerts) == 1
    assert "告警" in alerts[0]


def test_recovery_alerts_once():
    """故障→恢复跃迁：发一条恢复通知；之后健康轮次不再发。"""
    alerts = []
    mon = _make([False, True, True], alerts)
    mon.tick()
    mon.tick()
    mon.tick()
    assert len(alerts) == 2
    assert "恢复" in alerts[1]


def test_alert_failure_does_not_crash_monitor():
    """alert 回调自身抛异常（如飞书 API 抖动）不得炸掉监控状态机。"""
    calls = []
    it = iter([False, True])

    def bad_alert(_text):
        calls.append(1)
        raise RuntimeError("im api down")

    mon = DBHealthMonitor(probe=lambda: next(it), alert=bad_alert)
    assert mon.tick() is False
    assert mon.tick() is True
    assert len(calls) == 2


def test_backoff_grows_exponentially_and_caps():
    """故障期间 next_delay 指数翻倍，封顶 backoff_cap_sec。"""
    mon = _make([False, False, False, False], [],
                interval_sec=30, backoff_cap_sec=100)
    mon.tick()
    assert mon.next_delay == 30
    mon.tick()
    assert mon.next_delay == 60
    mon.tick()
    assert mon.next_delay == 100
    mon.tick()
    assert mon.next_delay == 100


def test_backoff_resets_on_recovery():
    """恢复后 next_delay 回到 interval_sec（下一轮按正常节奏探）。"""
    mon = _make([False, False, True], [],
                interval_sec=30, backoff_cap_sec=100)
    mon.tick()
    mon.tick()
    assert mon.next_delay == 60
    mon.tick()
    assert mon.next_delay == 30


def test_probe_exception_counts_as_down():
    """probe 抛异常（连接超时等）视为故障而非炸线程。"""
    alerts = []

    def boom():
        raise ConnectionError("pg unreachable")

    mon = DBHealthMonitor(probe=boom, alert=alerts.append)
    assert mon.tick() is False
    assert len(alerts) == 1


def test_probe_pg_url_fails_fast_on_dead_port():
    """真机回归（2026-09-09）：docker stop 后 engine.connect() 无限挂起，
    probe 必须走 psycopg 短超时直连。连 127.0.0.1:1（必拒）须快速抛异常。"""
    import time

    import pytest

    from gateway.health_monitor import probe_pg_url

    t0 = time.monotonic()
    with pytest.raises(Exception):
        probe_pg_url("postgresql+psycopg://u:p@127.0.0.1:1/db",
                     connect_timeout=3)
    assert time.monotonic() - t0 < 15
