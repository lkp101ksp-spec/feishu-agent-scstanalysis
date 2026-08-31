"""LocalExecutor：Docker + Kernel + ToolHandler 的本地实现。

submit 后异步启动线程跑 handler；list_active 返回 running 句柄。
Phase 2 简化：sync handler 直接调；async 由 Phase 2.1 接入 KernelManager.exec。
"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import UTC, datetime

from orchestrator.executor.executor_client import ExecutorClient
from orchestrator.executor.kernel_manager import KernelPool
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)


class LocalExecutor(ExecutorClient):
    def __init__(self, kernel_pool: KernelPool, tool_handler) -> None:
        self._kernel_pool = kernel_pool
        self._tool_handler = tool_handler
        self._handles: dict[str, TaskHandle] = {}
        # T1 无沙箱：首次 acquire 失败后置 False，后续 L1 节点跳过探测
        # （否则每个 L1 节点白等 30s docker 超时，真机 2026-08-30）
        self._kernel_available = True

    def submit(self, task: ExecutionTask) -> TaskHandle:
        handle = TaskHandle(
            execution_id=new_ulid(),
            task_id=task.task_id,
            node_id=task.node_id,
            state=ExecutionState.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._handles[handle.execution_id] = handle
        session_id = task.inputs.get("session_id") or task.task_id
        # risk_level 以注册表为准：ExecutionTask.risk_level 默认 L1_compute
        # 且 Scheduler 不传——L0 节点曾因此白等 30s docker 超时（真机 2026-08-30）
        risk = task.risk_level
        try:
            spec = self._tool_handler.registry.get(task.tool_name)
            if spec is not None:
                risk = spec.risk_level
        except Exception:
            pass
        if risk == "L1_compute" and self._kernel_available:
            # T1 无沙箱：acquire 失败（如 kernel 镜像未构建）仅告警不阻断——
            # 当前代码执行不经容器，容器只是生命周期句柄（Phase 13/T2 再强制）
            try:
                self._kernel_pool.acquire(session_id)
            except Exception as e:  # pragma: no cover - 依赖环境
                logger.warning("kernel acquire failed (no sandbox mode): %s", e)
                self._kernel_available = False
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
                # 失败必须留痕（真机 2026-08-30：n1 失败无任何日志，排障全靠猜）
                logger.warning(
                    "node %s tool %s failed: %s %s",
                    task.node_id, task.tool_name,
                    result.error_code, result.error_message,
                )
            else:
                handle.state = ExecutionState.SUCCESS
                handle.outputs = result.outputs
                handle.artifacts_ids = result.artifacts_ids
        except Exception as e:
            handle.state = ExecutionState.FAILED
            handle.error_code = "EXECUTOR_INTERNAL"
            handle.error_message = f"{e}\n{traceback.format_exc()}"
            logger.warning(
                "node %s tool %s internal error: %s",
                task.node_id, task.tool_name, e,
            )
        finally:
            handle.finished_at = datetime.now(UTC)

    def cancel(self, handle: TaskHandle) -> None:
        if handle.state == ExecutionState.RUNNING:
            handle.state = ExecutionState.CANCELLED
            handle.finished_at = datetime.now(UTC)

    def get_status(self, handle: TaskHandle) -> ExecutionState:
        return handle.state

    def list_active(self) -> list[TaskHandle]:
        return [h for h in self._handles.values() if h.state == ExecutionState.RUNNING]
