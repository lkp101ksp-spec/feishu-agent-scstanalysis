"""Jupyter Kernel 生命周期管理 + 按 session_id 复用。

- acquire(session_id) → 复用或新建 KernelHandle
- release(session_id) → 显式销毁
- idle_sweep() → 清理 idle 超时的 Kernel

Phase 2：抽象沙箱接口（start/stop），真实 KernelManager 接入由 Phase 2.1 完成。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class KernelHandle:
    kernel_id: str  # ULID/UUID
    session_id: str
    container_name: str
    started_at: datetime
    last_used_at: datetime = field(default_factory=datetime.utcnow)


class KernelPool:
    def __init__(self, sandbox, idle_timeout_sec: int = 1800) -> None:
        self._sandbox = sandbox
        self._idle_timeout = timedelta(seconds=idle_timeout_sec)
        self._handles: dict[str, KernelHandle] = {}

    def acquire(self, session_id: str) -> KernelHandle:
        existing = self._handles.get(session_id)
        if existing is not None:
            existing.last_used_at = datetime.utcnow()
            return existing
        container_name = self._sandbox.start(session_id)
        handle = KernelHandle(
            kernel_id=uuid.uuid4().hex,
            session_id=session_id,
            container_name=container_name,
            started_at=datetime.utcnow(),
        )
        self._handles[session_id] = handle
        return handle

    def release(self, session_id: str) -> None:
        h = self._handles.pop(session_id, None)
        if h:
            self._sandbox.stop(h.container_name)

    def touch(self, session_id: str) -> None:
        h = self._handles.get(session_id)
        if h:
            h.last_used_at = datetime.utcnow()

    def idle_sweep(self) -> int:
        now = datetime.utcnow()
        expired = [
            sid
            for sid, h in self._handles.items()
            if now - h.last_used_at > self._idle_timeout
        ]
        for sid in expired:
            self.release(sid)
        return len(expired)

    def get(self, session_id: str) -> Optional[KernelHandle]:
        return self._handles.get(session_id)
