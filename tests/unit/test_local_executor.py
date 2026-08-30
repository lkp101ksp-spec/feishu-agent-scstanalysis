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


def test_local_executor_risk_level_from_registry():
    """risk_level 以注册表为准：ExecutionTask 默认 L1_compute 且 Scheduler
    不传——L0 工具不得触发 kernel acquire（真机曾白等 30s docker 超时）。"""
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="read_x",
            description="d",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda doc_id: {"text": "x"},
        )
    )
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    # task 不传 risk_level（默认 L1_compute），工具实际是 L0
    task = ExecutionTask(
        task_id="t1", node_id="n1", tool_name="read_x",
        inputs={"doc_id": "d"},
    )
    h = ex.submit(task)
    time.sleep(0.1)
    assert pool.get(task.task_id) is None  # 未起容器
    assert ex.get_status(h) == ExecutionState.SUCCESS


def test_local_executor_kernel_failure_probed_once():
    """T1 无沙箱：首次 acquire 失败仅告警并记忆，后续 L1 节点不再探测。"""

    class BrokenSandbox:
        def __init__(self):
            self.start_calls = 0

        def start(self, session_id):
            self.start_calls += 1
            raise RuntimeError("docker timeout")

        def stop(self, name):
            pass

        def exec(self, *a, **kw):
            raise RuntimeError("no kernel")

    sandbox = BrokenSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="calc",
            description="d",
            parameters={"type": "object"},
            risk_level="L1_compute",
            handler=lambda **kw: {"ok": True},
        )
    )
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)

    for i in range(2):
        t = ExecutionTask(
            task_id=f"t{i}", node_id=f"n{i}", tool_name="calc",
            inputs={"session_id": f"s{i}"},
        )
        h = ex.submit(t)
        time.sleep(0.05)
        assert ex.get_status(h) == ExecutionState.SUCCESS

    assert sandbox.start_calls == 1  # 第二次不再探测
    assert ex._kernel_available is False


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
