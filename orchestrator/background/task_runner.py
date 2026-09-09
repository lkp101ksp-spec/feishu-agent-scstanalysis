"""BackgroundTaskRunner：简单协程轮询执行后台任务。

Phase 3：BindDocService.maybe_send_renew_card() 每 60s 跑一次。
Phase 5 替换为独立进程 + 持久化调度。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    def __init__(
        self,
        tasks: list[Callable[[], Awaitable[None]]],
        interval_sec: int = 60,
    ) -> None:
        self._tasks = tasks
        self.interval_sec = interval_sec
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run())

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while not self._stop.is_set():
            for task in self._tasks:
                try:
                    await task()
                except Exception as e:
                    logger.exception("background task failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                pass
