"""ExecutorClient 抽象接口。Phase 5 可换 GRpcExecutor 实现。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from shared.executor_types import ExecutionTask, TaskHandle


class ExecutorClient(ABC):
    @abstractmethod
    def submit(self, task: ExecutionTask) -> TaskHandle: ...

    @abstractmethod
    def cancel(self, handle: TaskHandle) -> None: ...

    @abstractmethod
    def get_status(self, handle: TaskHandle) -> object: ...

    @abstractmethod
    def list_active(self) -> list[TaskHandle]: ...