"""DB 健康监控：周期探活 + 状态跃迁告警 + 故障期指数退避。

2026-09-09 事故驱动：Docker Desktop 停运 → pg 不可达 → ws_client 进程
死亡且无人知晓，机器人静默离线数小时。本模块把监控逻辑抽成纯类
（probe/alert 依赖注入，单测友好）；线程壳在 ws_client 装配，告警经
IMAdapter 发 FEISHU_ADMIN_OPEN_IDS。
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class DBHealthMonitor:
    """DB 探活状态机：tick 一次探一轮，健康↔故障跃迁各发一条告警。

    故障期间 next_delay 指数翻倍至 backoff_cap_sec 封顶（免日志/告警轰炸）；
    恢复即回 interval_sec。probe/alert 回调抛异常一律吃掉——监控线程
    自身的健壮性优先于单轮动作成败。
    """

    def __init__(self, *, probe: Callable[[], bool],
                 alert: Callable[[str], None],
                 interval_sec: int = 30, backoff_cap_sec: int = 300):
        self._probe = probe
        self._alert = alert
        self._interval = interval_sec
        self._cap = backoff_cap_sec
        self._ok = True  # 初始按健康：首轮探到故障即产生"跃迁"告警
        self.next_delay = interval_sec

    def _fire(self, text: str) -> None:
        """发送告警；飞书 API 抖动不得炸掉状态机。"""
        try:
            self._alert(text)
        except Exception:
            logger.exception("db health alert send failed")

    def tick(self) -> bool:
        """探活一轮，返回当前是否健康；仅在状态跃迁时各告警一次。"""
        try:
            ok = bool(self._probe())
        except Exception:
            logger.exception("db health probe raised")
            ok = False
        if ok and not self._ok:
            logger.info("db health: RECOVERED")
            self._fire("[恢复] feishu-agent 数据库连接已恢复，机器人回复正常")
            self.next_delay = self._interval
        elif not ok and self._ok:
            logger.error("db health: DOWN")
            self._fire("[告警] feishu-agent 数据库连接失败，机器人回复可能异常；"
                       "请检查 Docker/pg 容器状态")
            self.next_delay = self._interval
        elif not ok:
            self.next_delay = min(self.next_delay * 2, self._cap)
        self._ok = ok
        return ok


def probe_pg_url(database_url: str, connect_timeout: int = 5) -> bool:
    """psycopg 短超时直连探活（与 tests/pg 探活同款，黑端口有界返回）。

    不走共享 engine：2026-09-09 真机复现 docker stop 后 engine.connect()
    无限挂起（pre_ping 借池内死连接做 ping 卡在 socket 读写），监控线程
    一旦被拖死就等于没有监控。libpq 只认 postgresql://（无 +psycopg）。
    """
    import psycopg

    raw = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(raw, connect_timeout=connect_timeout) as conn:
        conn.execute("SELECT 1")
    return True
