"""ws_client 启动期依赖等待（2026-09-11 事故驱动）。

事故背景：Docker Desktop 未起 → pg 容器不可达 → ws_client 启动期 DB
调用失败进程死亡；guardian 每分钟拉起连崩 189 次直到 Docker 人工拉起。
本模块把"等依赖就绪再启动"抽成纯函数（probe/clock/sleep 注入，
单测友好）；等待超上限 SystemExit 非零退出，guardian 拉起重新等——
避免进程带过期上下文无限挂起。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


def wait_for_dependency(probe: Callable[[], bool], *,
                        interval_sec: float = 5,
                        cap_sec: float = 1800,
                        log_every_sec: float = 60,
                        clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep,
                        name: str = "dependency") -> float:
    """阻塞等待依赖就绪，返回等待秒数；超 cap 抛 SystemExit(3)。

    probe 抛异常一律按未就绪处理（依赖抖动不得炸掉等待循环）；
    等待期间日志节流：首失败 WARNING 一条，之后每 log_every_sec 一条，
    就绪后 INFO 记总耗时（未等待则静默返回 0）。
    """
    start = clock()
    last_log = start
    try:
        if probe():
            return 0.0
    except Exception as e:
        logger.warning("startup wait: %s probe raised: %s", name, e)
    logger.warning("startup wait: %s not ready, retrying every %ss "
                   "(cap %ss)", name, interval_sec, cap_sec)
    while True:
        sleep(interval_sec)
        now = clock()
        waited = now - start
        if waited >= cap_sec:
            logger.error("startup wait: %s still not ready after %.0fs "
                         "(cap %ss), exit for guardian relaunch",
                         name, waited, cap_sec)
            raise SystemExit(3)
        try:
            ready = bool(probe())
        except Exception as e:
            logger.warning("startup wait: %s probe raised: %s", name, e)
            ready = False
        if ready:
            logger.info("startup wait: %s ready after %.0fs", name, waited)
            return waited
        if now - last_log >= log_every_sec:
            last_log = now
            logger.warning("startup wait: %s not ready (%.0fs elapsed)",
                           name, waited)
