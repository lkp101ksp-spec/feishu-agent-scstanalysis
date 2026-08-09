"""Executor 共享类型：状态机、Task/Handle dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ExecutionState(str, Enum):
    """单个节点执行的完整生命周期状态机。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    DENIED = "denied"


@dataclass
class ExecutionTask:
    """Scheduler 提交给 Executor 的最小单元。"""

    task_id: str
    node_id: str
    tool_name: str
    inputs: dict
    risk_level: str = "L1_compute"
    timeout_sec: int = 60
    max_retries: int = 1
    artifact_id: Optional[str] = None  # 上传 Drive 时填


@dataclass
class TaskHandle:
    """Executor 返回的执行句柄。"""

    execution_id: str  # ULID
    task_id: str
    node_id: str
    state: ExecutionState
    started_at: datetime
    finished_at: Optional[datetime] = None
    outputs: Optional[dict] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    artifacts_ids: list[str] = field(default_factory=list)