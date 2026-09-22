"""WsGuardian 单测：ws_client 死亡检测 + 拉起 + 崩溃循环保护。

2026-09-09 事故驱动：ws_client 进程死亡后无人拉起，机器人静默离线。
守护逻辑抽成纯类（is_alive/restart/clock 注入），线程壳只做 sleep 循环。
"""
from scripts.ws_guardian import WsGuardian


def test_alive_no_restart():
    """ws_client 活着：不动作。"""
    restarts = []
    g = WsGuardian(is_alive=lambda: True, restart=lambda: restarts.append(1))
    assert g.tick() is False
    assert restarts == []


def test_dead_triggers_restart():
    """首次发现死亡：立即拉起。"""
    restarts = []
    g = WsGuardian(is_alive=lambda: False, restart=lambda: restarts.append(1))
    assert g.tick() is True
    assert len(restarts) == 1


def test_crash_loop_protection():
    """拉起后立刻又死（崩溃循环）：min_restart_interval 内不重复拉起。"""
    restarts = []
    now = [1000.0]
    g = WsGuardian(is_alive=lambda: False, restart=lambda: restarts.append(1),
                   min_restart_interval_sec=30, clock=lambda: now[0])
    assert g.tick() is True
    now[0] += 10
    assert g.tick() is False
    now[0] += 10
    assert g.tick() is False
    assert len(restarts) == 1


def test_restart_again_after_interval():
    """间隔过后仍死：允许再次拉起（不是永久放弃）。"""
    restarts = []
    now = [1000.0]
    g = WsGuardian(is_alive=lambda: False, restart=lambda: restarts.append(1),
                   min_restart_interval_sec=30, clock=lambda: now[0])
    g.tick()
    now[0] += 31
    assert g.tick() is True
    assert len(restarts) == 2


def test_restart_exception_does_not_crash_guardian():
    """restart 自身失败（如解释器路径丢失）：记录时间戳防轰炸，不炸守护。"""
    calls = []
    now = [1000.0]

    def bad_restart():
        calls.append(1)
        raise OSError("spawn failed")

    g = WsGuardian(is_alive=lambda: False, restart=bad_restart,
                   min_restart_interval_sec=30, clock=lambda: now[0])
    assert g.tick() is False
    now[0] += 10
    assert g.tick() is False  # 仍在冷却窗内
    assert len(calls) == 1


def test_guardian_singleton_refuses_second_instance(tmp_path, monkeypatch):
    """guardian 单实例守卫：pidfile 指向活进程时拒绝启动（SystemExit）。"""
    import pytest

    from scripts import ws_guardian

    pidfile = tmp_path / ".ws_guardian.pid"
    pidfile.write_text("1234")
    monkeypatch.setattr(ws_guardian, "_GUARDIAN_PIDFILE", pidfile)
    monkeypatch.setattr(ws_guardian, "_pid_alive", lambda pid: True)
    with pytest.raises(SystemExit):
        ws_guardian.acquire_guardian_singleton()


def test_guardian_singleton_takes_over_stale(tmp_path, monkeypatch):
    """pidfile 陈旧（进程已死）：接管并写入本进程 pid。"""
    import os

    from scripts import ws_guardian

    pidfile = tmp_path / ".ws_guardian.pid"
    pidfile.write_text("99999")
    monkeypatch.setattr(ws_guardian, "_GUARDIAN_PIDFILE", pidfile)
    monkeypatch.setattr(ws_guardian, "_pid_alive", lambda pid: False)
    ws_guardian.acquire_guardian_singleton()
    assert pidfile.read_text().strip() == str(os.getpid())


def test_restart_captures_stderr_and_injects_utf8(tmp_path, monkeypatch):
    """_restart：子进程 stderr/stdout 落盘 + PYTHONUTF8=1 注入。

    2026-09-13 事故：CREATE_NO_WINDOW pythonw 下启动期 traceback 全丢，
    连崩两天零线索；locale GBK 隐式 open() 解码炸需 PYTHONUTF8 纵深防御。
    """
    import sys
    from typing import Any

    from scripts import ws_guardian

    calls: list[dict[str, Any]] = []

    class _FakePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            calls.append({"cmd": cmd, **kwargs})

    monkeypatch.setattr(ws_guardian.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(ws_guardian, "_REPO_ROOT", tmp_path)
    ws_guardian._restart()
    assert calls, "Popen not called"
    kw = calls[0]
    assert kw["cmd"] == [sys.executable, "-m", "gateway.ws_client"]
    assert kw["cwd"] == str(tmp_path)
    assert kw["stderr"] is kw["stdout"]  # 同一句柄合并落盘
    assert (tmp_path / "logs" / "ws_client_stderr.log").exists()
    assert kw["env"]["PYTHONUTF8"] == "1"

def test_write_heartbeat(tmp_path, monkeypatch):
    """heartbeat 落 ISO 时间戳（2026-09-22 僵尸双实例治理：外部可判假死）。"""
    from datetime import datetime

    from scripts import ws_guardian
    hb = tmp_path / "logs" / "ws_guardian.heartbeat"
    ws_guardian.write_heartbeat(hb)
    assert hb.exists()
    datetime.fromisoformat(hb.read_text(encoding="utf-8"))  # 非法格式即抛


def test_kill_other_guardians_non_win32_noop(monkeypatch):
    """非 Windows 平台安全跳过（不发起任何子进程）。"""
    from scripts import ws_guardian
    monkeypatch.setattr(ws_guardian.sys, "platform", "linux")

    def _boom(*a, **k):
        raise AssertionError("Popen should not be called")

    monkeypatch.setattr(ws_guardian.subprocess, "Popen", _boom)
    assert ws_guardian.kill_other_guardians() is None


def test_kill_other_guardians_spawn_failure_safe(monkeypatch):
    """Windows 下 spawn 失败仅记日志不抛出（异步清扫，fire-and-forget）。"""
    from scripts import ws_guardian
    monkeypatch.setattr(ws_guardian.sys, "platform", "win32")

    def _boom(*a, **k):
        raise OSError("no powershell")

    monkeypatch.setattr(ws_guardian.subprocess, "Popen", _boom)
    assert ws_guardian.kill_other_guardians() is None
