"""ApprovalBroker：审批卡片决策的跨线程传递（Phase 14 板块②）。

research 后台线程 wait() 阻塞等待；ws 卡片回调线程 decide() 写入决策。
单进程内存实现：首次决策固化（幂等，重复点击不二次生效），wait 取走
后清理条目防泄漏；进程重启后无人在等 → decide 仍记录但无人消费，
等待方由超时兜底取消（安全侧失败）。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_VALID_DECISIONS = ("approve", "deny")


class ApprovalBroker:
    """doc_write_id → 决策（threading.Event 跨线程唤醒）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        # doc_write_id -> (decision, operator)：先决策后等待时暂存
        self._decisions: dict[str, tuple[str, str]] = {}

    def wait(self, doc_write_id: str, timeout: float) -> str | None:
        """阻塞等待决策：返回 "approve"/"deny"，超时返回 None。"""
        with self._lock:
            # 决策先于 wait 到达（发卡片后用户秒点）：直接取走
            if doc_write_id in self._decisions:
                return self._decisions.pop(doc_write_id)[0]
            ev = threading.Event()
            self._events[doc_write_id] = ev
        if not ev.wait(timeout):
            with self._lock:
                self._events.pop(doc_write_id, None)
                # 超时瞬间决策到达：以决策为准
                if doc_write_id in self._decisions:
                    return self._decisions.pop(doc_write_id)[0]
            return None
        with self._lock:
            self._events.pop(doc_write_id, None)
            decision, operator = self._decisions.pop(doc_write_id)
        logger.info(
            "approval decided: doc_write_id=%s decision=%s operator=%s",
            doc_write_id, decision, operator,
        )
        return decision

    def decide(self, doc_write_id: str, decision: str,
               operator: str = "") -> bool:
        """回调线程写入决策；首次生效返回 True，重复/非法返回 False。"""
        if decision not in _VALID_DECISIONS:
            return False
        with self._lock:
            if doc_write_id in self._decisions:
                return False  # 已决策：幂等拒绝（重复点击不二次生效）
            self._decisions[doc_write_id] = (decision, operator)
            ev = self._events.get(doc_write_id)
        if ev is not None:
            ev.set()
        return True
