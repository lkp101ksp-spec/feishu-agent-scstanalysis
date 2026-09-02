"""ws_client pidfile 单实例守卫单测（Phase 22 T1）。

_pid_alive/acquire 均可注入：monkeypatch gateway.ws_client 模块的
_PIDFILE（tmp_path）与 _pid_alive（免真进程）。
"""
from __future__ import annotations

import pytest

import gateway.ws_client as wsc


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    """隔离 pidfile 到 tmp_path，_pid_alive 默认报死（stale）。"""
    p = tmp_path / ".ws_client.pid"
    monkeypatch.setattr(wsc, "_PIDFILE", p)
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: False)
    return p


def test_first_start_writes_pid(pidfile):
    """无 pidfile 首启：写入当前 pid。"""
    wsc.acquire_single_instance()
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_alive_instance_rejects(pidfile, monkeypatch):
    """活实例：默认拒绝启动（SystemExit 1），pidfile 不被覆盖。"""
    pidfile.write_text("12345")
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: True)
    with pytest.raises(SystemExit) as ei:
        wsc.acquire_single_instance()
    assert ei.value.code == 1
    assert pidfile.read_text().strip() == "12345"


def test_stale_pidfile_takeover(pidfile):
    """stale（进程已死）：警告并接管覆写。"""
    pidfile.write_text("12345")
    wsc.acquire_single_instance()
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_force_terminates_and_takes_over(pidfile, monkeypatch):
    """--force：杀旧（mock _terminate）后接管。"""
    pidfile.write_text("12345")
    monkeypatch.setattr(wsc, "_pid_alive", lambda pid: True)
    killed = []
    monkeypatch.setattr(wsc, "_terminate", lambda pid: killed.append(pid))
    wsc.acquire_single_instance(force=True)
    assert killed == [12345]
    assert pidfile.read_text().strip() == str(wsc.os.getpid())


def test_release_only_removes_own_pidfile(pidfile):
    """atexit 清理：pidfile 已被接管者覆写时不误删。"""
    wsc.acquire_single_instance()
    pidfile.write_text("99999")  # 模拟被接管
    wsc._release_pidfile()
    assert pidfile.exists()
    pidfile.write_text(str(wsc.os.getpid()))
    wsc._release_pidfile()
    assert not pidfile.exists()
