"""ws_client 守护进程：周期探活 pidfile，进程死亡则自动拉起。

入口：python scripts/ws_guardian.py（生产建议注册 Windows 计划任务随登录
启动，见 测试总结/ROADMAP 运维守则）。2026-09-09 事故驱动：ws_client
死亡后无人拉起，机器人静默离线。守护逻辑抽成纯类 WsGuardian
（is_alive/restart/clock 注入，单测友好）；min_restart_interval 防
"拉起即崩"的崩溃循环把日志和进程表打爆。
"""
from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIDFILE = _REPO_ROOT / ".ws_client.pid"
_GUARDIAN_PIDFILE = _REPO_ROOT / ".ws_guardian.pid"
_HEARTBEAT = _REPO_ROOT / "logs" / "ws_guardian.heartbeat"


def _pid_alive(pid: int) -> bool:
    """复用 ws_client 的 Windows 探活（guardian 同属 Windows 生产路径）。"""
    from gateway.ws_client import _pid_alive as ws_pid_alive
    return ws_pid_alive(pid)


def acquire_guardian_singleton() -> None:
    """guardian 单实例守卫：活实例拒绝（SystemExit），stale 接管写本进程 pid。

    计划任务随登录启动 + 人工手动启动可能双开；双 guardian 会竞争拉起
    ws_client（虽有 ws_client 侧单实例兜底，但日志会充满误判）。
    """
    if _GUARDIAN_PIDFILE.exists():
        try:
            old_pid = int(_GUARDIAN_PIDFILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            logger.error("ws_guardian already running (pid=%s)", old_pid)
            raise SystemExit(1)
    _GUARDIAN_PIDFILE.write_text(str(os.getpid()))
    atexit.register(_release_guardian_pidfile)


def _release_guardian_pidfile() -> None:
    """退出清理：仅当 pidfile 内容仍是本进程 pid（不误删接管者）。"""
    try:
        if _GUARDIAN_PIDFILE.read_text().strip() == str(os.getpid()):
            _GUARDIAN_PIDFILE.unlink(missing_ok=True)
    except OSError:
        pass


def kill_other_guardians() -> int:
    """单实例加固（2026-09-22 僵尸双实例治理）：清理其它 ws_guardian 进程。

    pidfile 守卫有盲区（两实例并存且均停止 tick 的真机事故）：启动后按
    命令行特征（含 ws_guardian）清扫同族非本进程。仅 Windows 生产路径
    生效，其余平台/查询失败均安全返回 0。
    """
    if sys.platform != "win32":
        return 0
    ps = ("$ps = Get-CimInstance Win32_Process -Filter \"Name like 'python%'\""
          " | Where-Object { $_.CommandLine -match 'ws_guardian' -and"
          f" $_.ProcessId -ne {os.getpid()} }}"
          "; $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
          "; @($ps).Count")
    try:
        out = subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30)
        n = int((out.stdout or "").strip() or "0")
    except Exception:  # noqa: BLE001 —— 清扫失败不影响本实例守护
        logger.exception("kill_other_guardians failed")
        return 0
    if n:
        logger.warning("killed %d zombie guardian(s)", n)
    return n


def write_heartbeat(path: Path = _HEARTBEAT) -> None:
    """每 tick 落一次 ISO 时间戳——外部可据文件新旧判 guardian 是否假死
    （2026-09-22 事故：双僵尸实例日志静默数小时无人察觉）。"""
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(datetime.now().isoformat(timespec="seconds"),
                        encoding="utf-8")
    except OSError:
        pass


class WsGuardian:
    """守护状态机：tick 一次探一轮，死了就拉起，冷却窗内不重复拉起。

    restart 失败（如解释器路径丢失）同样记录时间戳进冷却窗——
    失败风暴比晚 30 秒重试危害更大。
    """

    def __init__(self, *, is_alive: Callable[[], bool],
                 restart: Callable[[], None],
                 min_restart_interval_sec: int = 30,
                 clock: Callable[[], float] = time.monotonic):
        self._is_alive = is_alive
        self._restart = restart
        self._min_interval = min_restart_interval_sec
        self._clock = clock
        self._last_attempt: Optional[float] = None

    def tick(self) -> bool:
        """探活一轮：活着→False；死亡且过冷却窗→拉起并返回 True。"""
        if self._is_alive():
            return False
        now = self._clock()
        if (self._last_attempt is not None
                and now - self._last_attempt < self._min_interval):
            return False
        self._last_attempt = now
        try:
            self._restart()
        except Exception:
            logger.exception("ws_client restart failed")
            return False
        logger.warning("ws_client dead -> restarted")
        return True


def _ws_alive() -> bool:
    """pidfile 探活：文件缺失/损坏/pid 已死一律视为需要拉起。"""
    if not _PIDFILE.exists():
        return False
    try:
        pid = int(_PIDFILE.read_text().strip())
    except (ValueError, OSError):
        return False
    from gateway.ws_client import _pid_alive
    return _pid_alive(pid)


def _restart() -> None:
    """拉起新 ws_client（Windows 隐藏窗口；单实例守卫由 ws_client 自理）。

    stderr/stdout 落盘 logs/ws_client_stderr.log——2026-09-13 事故：启动期
    traceback 在 CREATE_NO_WINDOW pythonw 下全丢，连崩两天零线索；
    PYTHONUTF8=1 注入防 locale GBK 隐式 open() 解码炸（纵深防御）。
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    log_dir = _REPO_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1"}
    with open(log_dir / "ws_client_stderr.log", "ab") as err:
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "gateway.ws_client"],
            cwd=str(_REPO_ROOT), env=env, stdout=err, stderr=err, **kwargs)


def main(interval_sec: int = 60) -> None:
    """守护入口：文件日志 + 每 interval 秒探活一轮。"""
    log_dir = _REPO_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "ws_guardian.log",
                                  maxBytes=1024 * 1024, backupCount=2,
                                  encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )
    acquire_guardian_singleton()
    killed = kill_other_guardians()
    guardian = WsGuardian(is_alive=_ws_alive, restart=_restart)
    logger.info("ws guardian started (interval=%ss, zombies_killed=%d)",
                interval_sec, killed)
    while True:
        guardian.tick()
        write_heartbeat()
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
