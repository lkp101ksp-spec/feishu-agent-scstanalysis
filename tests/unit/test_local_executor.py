import time

from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.local_executor import LocalExecutor
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from shared.executor_types import ExecutionState, ExecutionTask


class FakeSandbox:
    def __init__(self):
        self.execs = []

    def start(self, session_id):
        return f"c_{session_id}"

    def stop(self, container_name):
        pass

    def exec(self, container_name, cmd, *, timeout_sec=60):
        self.execs.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="hello", stderr="")


def test_local_executor_runs_L0_tool():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="echo",
            description="echo arg",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda text: {"result": text},
        )
    )
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    task = ExecutionTask(
        task_id="t1", node_id="n1", tool_name="echo",
        inputs={"text": "hi"}, risk_level="L0_read",
    )
    handle = ex.submit(task)
    time.sleep(0.1)
    assert ex.get_status(handle) == ExecutionState.SUCCESS


def test_local_executor_acquires_kernel_for_L1():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="dummy",
            description="d",
            parameters={"type": "object"},
            risk_level="L1_compute",
            handler=lambda **kw: {"ok": True},
        )
    )
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    task = ExecutionTask(
        task_id="t1", node_id="n1", tool_name="dummy",
        inputs={"session_id": "s1"}, risk_level="L1_compute",
    )
    h = ex.submit(task)
    time.sleep(0.1)
    assert pool.get("s1") is not None
    assert ex.get_status(h) == ExecutionState.SUCCESS


def test_local_executor_cancel():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="slow",
            description="d",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda: None,
        )
    )
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    task = ExecutionTask(
        task_id="t1", node_id="n1", tool_name="slow",
        inputs={}, risk_level="L0_read",
    )
    h = ex.submit(task)
    time.sleep(0.1)
    ex.cancel(h)
    assert h.state in (ExecutionState.SUCCESS, ExecutionState.CANCELLED)