"""Phase 23：ws_client bio workspace GC sweeper 单测（沿用 scanner 模式）。"""
from types import SimpleNamespace

import gateway.ws_client as wsc


def _settings(**over):
    """构造只含 GC 相关字段的 settings 桩。"""
    base = dict(
        bio_workspace_gc_enabled=True,
        bio_workspace_gc_interval_sec=3600,
        bio_workspace_root="./bio_workspace",
        bio_workspace_ttl_sec=604800,
        bio_workspace_cap_gb=10,
        bio_workspace_grace_sec=7200,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_sweeper_disabled_returns_none():
    """enabled=False 不启动线程。"""
    assert wsc.start_bio_workspace_gc_sweeper(
        _settings(bio_workspace_gc_enabled=False)) is None


def test_sweeper_interval_zero_returns_none():
    """interval=0 不启动线程。"""
    assert wsc.start_bio_workspace_gc_sweeper(
        _settings(bio_workspace_gc_interval_sec=0)) is None


def test_sweeper_starts_daemon_thread():
    """正常启动：返回 daemon 线程，命名 bio-workspace-gc。"""
    t = wsc.start_bio_workspace_gc_sweeper(_settings())
    try:
        assert t is not None and t.daemon and t.name == "bio-workspace-gc"
    finally:
        pass  # daemon 线程随 pytest 进程退出，无需 join


def test_sweep_once_passes_settings(monkeypatch):
    """单轮 sweep 把 settings 正确换算传给 gc.sweep（GB→bytes）。"""
    calls = []
    fake_result = {"ttl_deleted": [], "lru_deleted": [],
                   "freed_bytes": 0, "skipped": []}
    monkeypatch.setattr(
        wsc, "sweep", lambda *a, **k: calls.append((a, k)) or fake_result)
    wsc._bio_gc_sweep_once(_settings())
    (args, kwargs) = calls[0]
    assert args == ("./bio_workspace",)
    assert kwargs["ttl_sec"] == 604800
    assert kwargs["cap_bytes"] == 10 * (1024 ** 3)
    assert kwargs["grace_sec"] == 7200
