"""LocalExecutor：Docker + Kernel + ToolHandler 的本地实现。

submit 后异步启动线程跑 handler；list_active 返回 running 句柄。
Phase 2 简化：sync handler 直接调；async 由 Phase 2.1 接入 KernelManager.exec。
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

from orchestrator.executor.executor_client import ExecutorClient
from orchestrator.executor.kernel_manager import KernelPool
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle
from shared.ulid_ import new_ulid


class LocalExecutor(ExecutorClient):
    def __init__(self, kernel_pool: KernelPool, tool_handler) -> None:
        self._kernel_pool = kernel_pool
        self._tool_handler = tool_handler
        self._handles: dict[str, TaskHandle] = {}

    def submit(self, task: ExecutionTask) -> TaskHandle:
        handle = TaskHandle(
            execution_id=new_ulid(),
            task_id=task.task_id,
            node_id=task.node_id,
            state=ExecutionState.RUNNING,
            started_at=datetime.utcnow(),
        )
        self._handles[handle.execution_id] = handle
        session_id = task.inputs.get("session_id") or task.task_id
        if task.risk_level == "L1_compute":
            self._kernel_pool.acquire(session_id)
        t = threading.Thread(
            target=self._run, args=(handle, task), daemon=True
        )
        t.start()
        return handle

    def _run(self, handle: TaskHandle, task: ExecutionTask) -> None:
        try:
            result = self._tool_handler.execute(
                task.tool_name,
                task.inputs,
                session_id=task.inputs.get("session_id", ""),
            )
            if result.error_code:
                handle.state = ExecutionState.FAILED
                handle.error_code = result.error_code
                handle.error_message = result.error_message
            else:
                handle.state = ExecutionState.SUCCESS
                handle.outputs = result.outputs
                handle.artifacts_ids = result.artifacts_ids
        except Exception as e:
            handle.state = ExecutionState.FAILED
            handle.error_code = "EXECUTOR_INTERNAL"
            handle.error_message = f"{e}\n{traceback.format_exc()}"
        finally:
            handle.finished_at = datetime.utcnow()

    def cancel(self, handle: TaskHandle) -> None:
        if handle.state == ExecutionState.RUNNING:
            handle.state = ExecutionState.CANCELLED
            handle.finished_at = datetime.utcnow()

    def get_status(self, handle: TaskHandle) -> ExecutionState:
        return handle.state

    def list_active(self) -> list[TaskHandle]:
        return [h for h in self._handles.values() if h.state == ExecutionState.RUNNING]
