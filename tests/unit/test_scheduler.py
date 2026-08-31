import asyncio
import datetime as dt

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import PlanResult, Scheduler
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


class FakeExecutor:
    def __init__(self):
        self.submitted: list[ExecutionTask] = []
        self.handles: dict[str, TaskHandle] = {}

    def submit(self, task: ExecutionTask) -> TaskHandle:
        h = TaskHandle(
            execution_id=f"e_{len(self.submitted)}",
            task_id=task.task_id,
            node_id=task.node_id,
            state=ExecutionState.RUNNING,
            started_at=dt.datetime.utcnow(),
        )
        self.submitted.append(task)
        self.handles[h.execution_id] = h
        return h

    def get_status(self, handle):
        return handle.state

    def cancel(self, handle):
        handle.state = ExecutionState.CANCELLED

    def list_active(self):
        return [h for h in self.handles.values() if h.state == ExecutionState.RUNNING]


def make_plan() -> DAGPlan:
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=[])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={"x": "n1.result"}, depends_on=["n1"])
    return DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])


async def test_scheduler_runs_sequentially():
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)

    async def drive():
        for _ in range(50):
            await asyncio.sleep(0.005)
            # n1 first
            for h in ex.handles.values():
                if h.state == ExecutionState.RUNNING and h.finished_at is None:
                    h.state = ExecutionState.SUCCESS
                    h.outputs = {"result": "ok"}
                    h.finished_at = dt.datetime.utcnow()
            if sch._all_terminal():
                return

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    assert ex.handles["e_1"].state == ExecutionState.SUCCESS
    assert result.status == "success"


async def test_scheduler_continues_on_failure():
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="a",
                 inputs={}, depends_on=[], on_node_fail="continue")
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="b",
                 inputs={"x": "n1.result"}, depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)

    async def drive():
        for _ in range(50):
            await asyncio.sleep(0.005)
            for h in ex.handles.values():
                if h.state == ExecutionState.RUNNING and h.finished_at is None:
                    h.state = ExecutionState.FAILED
                    h.error_code = "X"
                    h.finished_at = dt.datetime.utcnow()
            if sch._all_terminal():
                return

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    # n2 因上游失败被 SKIPPED
    assert any(s == ExecutionState.SKIPPED for s in result.node_states.values())
    assert result.status == "success_with_partial_failure"


def test_scheduler_init_stores_plan():
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    assert sch.plan.plan_id == "p"
    assert sch.max_concurrent == 4


def test_resolve_inputs_alias_fallback():
    """引用字段缺失时按别名回退（records/text/results/summary）。

    真机 2026-08-30：模型把 blast 输出 records 猜成 summary，
    下游拿到 None 导致摘要空转。
    """
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    # 上游输出只有 records（无 result/summary）
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"records": [{"title": "BRCA1"}]},
    )
    resolved = sch._resolve_inputs(plan.nodes[1])
    assert resolved["x"] == [{"title": "BRCA1"}]  # n1.result → 回退 n1.records


def test_resolve_inputs_missing_without_alias_is_none():
    """无别名可回退时保持 None（不抛错）。"""
    plan = make_plan()
    ex = FakeExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"answer": 42},
    )
    resolved = sch._resolve_inputs(plan.nodes[1])
    assert resolved["x"] is None


def test_resolve_inputs_dot_literal_not_reference():
    """含 `.` 的普通字符串是字面值，不是引用——`.` 前必须是已知 node_id。

    真机 2026-08-30：run_python 的 code 含 `{float(v):.6e}`，整段被误拆成
    引用置 None → 容器空文件假 success → result=None。
    """
    code = (
        'value = 2 ** 100\n'
        'print(f"科学计数法 ≈ {float(value):.6e}")\n'
        '{"value": value, "digits": len(str(value))}\n'
    )
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={"code": code}, depends_on=[])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="summarize_text",
                 inputs={"x": "n1.result"}, depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=FakeExecutor())
    assert sch._resolve_inputs(n1)["code"] == code  # 原样透传
    # 真引用（已知 node_id 前缀）行为不变
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"result": "1267...5376"},
    )
    assert sch._resolve_inputs(n2)["x"] == "1267...5376"


def test_resolve_inputs_inline_reference_in_code():
    """code 内嵌的 <node>.<field> 替换为 repr(值)（Python 字面量）。

    真机 2026-08-30：模型写 len('n1.text') 期望引用在代码内生效，
    整值替换语义覆盖不到 → 算了字面量 'n1.text' 的长度 7。
    """
    doc = "单细胞分析结果\n1 测试"
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="read_doc",
                 inputs={}, depends_on=[])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="run_python",
                 inputs={"code": "result = len('n1.text') > 500"}, depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=FakeExecutor())
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"text": doc, "records": [1, 2]},
    )
    resolved = sch._resolve_inputs(n2)
    # 引号内形态：保留外层引号、替换内核（repr 的转义内容）
    assert resolved["code"] == f"result = len('{repr(doc)[1:-1]}') > 500"
    # 无引号形态：整段 repr
    n2b = DAGNode(node_id="n2b", kind="tool", tool_name="run_python",
                  inputs={"code": "len(n1.text)"}, depends_on=["n1"])
    plan.nodes.append(n2b)
    assert sch._resolve_inputs(n2b)["code"] == f"len({doc!r})"
    # 非节点前缀（np.arange）/字段不存在（n1.nofield）不受影响
    n3 = DAGNode(node_id="n3", kind="tool", tool_name="run_python",
                 inputs={"code": "import numpy as np\nnp.arange(n1.nofield).max()"},
                 depends_on=["n1"])
    plan.nodes.append(n3)
    code3 = sch._resolve_inputs(n3)["code"]
    assert "np.arange" in code3
    assert "n1.nofield" in code3  # 字段不存在 → 保留原文（执行时显式报错）


def test_resolve_inputs_dict_result_injects_dict_literal():
    """上游 result 为原生 dict 时，内嵌注入的是 dict 字面量（非带引号字符串）。

    真机 2026-08-31 综合演练：exec_code 的 result 曾一律为 repr 字符串，
    下游 `data = n2.result` 拿到字符串字面量，`data["percentage"]` 抛
    TypeError（b1_tn3 PY_RUNTIME_ERROR）；result 还原原生类型后本用例
    守护注入链路闭环。容器 repr 加括号包裹守护 f-string 场景（b1_tt1）。
    """
    import ast as _ast

    stats = {"fetched": 5, "total": 67407, "percentage": 0.01}
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={}, depends_on=[])
    n2 = DAGNode(node_id="n2", kind="tool", tool_name="run_python",
                 inputs={"code": "data = n1.result\nmsg = data['percentage']"},
                 depends_on=["n1"])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n2], entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=FakeExecutor())
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS, started_at=dt.datetime.utcnow(),
        finished_at=dt.datetime.utcnow(),
        outputs={"result": stats},
    )
    resolved = sch._resolve_inputs(n2)
    assert resolved["code"] == f"data = ({stats!r})\nmsg = data['percentage']"

    def _first_expr(code: str) -> _ast.expr:
        """首条语句必须是表达式且求值可取字段（AST 结构级断言）。"""
        body = _ast.parse(code, mode="exec").body
        assign = body[0]
        assert isinstance(assign, _ast.Assign)
        return assign.value

    # 加括号后仍是 dict 字面量（而非 Constant 字符串）
    assert isinstance(_first_expr(resolved["code"]), _ast.Dict)

    # f-string 场景（b1_tt1 真机失败模式）：`{` 后注入 dict 不得产生 {{ 转义
    n3 = DAGNode(node_id="n3", kind="tool", tool_name="run_python",
                 inputs={"code": 'print(f"样本充足，取回比例：{n1.result["percentage"]}%")'},
                 depends_on=["n1"])
    plan.nodes.append(n3)
    code3 = sch._resolve_inputs(n3)["code"]
    # ast_guard 对注入后代码 ast.parse——语法必须成立
    stmt3 = _ast.parse(code3, mode="exec").body[0]
    fstr = stmt3.value.args[0]  # print(f"...") 的参数
    assert isinstance(fstr, _ast.JoinedStr)  # 仍是 f-string
    inner = fstr.values[1]  # {(...)} 表达式段
    assert isinstance(inner.value, _ast.Subscript)  # ({...})["percentage"]
    # 求值闭环：取出的字段值正确
    assert eval(compile(_ast.Expression(inner.value), "<t>", "eval")) == 0.01


# === 节点自愈（综合演练轮）：代码级失败 LLM 修复重试 ===

class FlakyExecutor(FakeExecutor):
    """前 N 次 submit 直接 FAILED（可修复错误码），其余交由驱动置 SUCCESS。"""

    def __init__(self, fail_first: int, error_code: str = "PY_RUNTIME_ERROR"):
        super().__init__()
        self._fail_left = fail_first
        self._error_code = error_code

    def submit(self, task: ExecutionTask) -> TaskHandle:
        h = super().submit(task)
        if self._fail_left > 0:
            self._fail_left -= 1
            h.state = ExecutionState.FAILED
            h.error_code = self._error_code
            h.error_message = "TypeError: string indices must be integers"
            h.finished_at = dt.datetime.utcnow()
        return h


def _repair_llm(fixed: str):
    """假修复 LLM：记录调用，恒返 fixed（剥围栏路径用真围栏也测一次）。"""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.chat.return_value = f"```python\n{fixed}\n```"
    return llm


async def _run_with_drive(sch: Scheduler) -> PlanResult:
    """后台协程把 RUNNING 句柄翻成 SUCCESS，主协程等计划终态。"""
    async def drive():
        for _ in range(500):
            await asyncio.sleep(0.005)
            for h in ex.handles.values():
                if h.state == ExecutionState.RUNNING and h.finished_at is None:
                    h.state = ExecutionState.SUCCESS
                    h.finished_at = dt.datetime.utcnow()
            if sch._all_terminal():
                return

    ex = sch.executor
    asyncio.create_task(drive())
    return await asyncio.wait_for(sch.run_until_done(), timeout=3.0)


async def test_node_repair_recovers_run_python_failure():
    """自愈：run_python 代码级失败 → LLM 修 code（剥围栏）→ 重跑 SUCCESS。"""
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={"code": "data = n1.result\ndata['k']"}, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1], entry_node_ids=["n1"])
    ex = FlakyExecutor(fail_first=1)
    llm = _repair_llm("data = {'k': 1}\ndata['k']")
    sch = Scheduler(plan=plan, executor=ex, code_repair_llm=llm)
    result = await _run_with_drive(sch)
    assert result.node_states["n1"] == ExecutionState.SUCCESS
    assert result.status == "success"
    assert n1.inputs["code"] == "data = {'k': 1}\ndata['k']"  # 围栏已剥
    assert len(ex.submitted) == 2  # 失败 1 次 + 自愈重跑 1 次
    assert llm.chat.call_count == 1


async def test_node_repair_limit_and_error_filter():
    """超上限不修 / 非可修复错误码不修 / LLM 缺失不修——保持 FAILED。"""
    # ① 上限 0（关闭自愈）
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={"code": "1/0"}, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1], entry_node_ids=["n1"])
    ex = FlakyExecutor(fail_first=1)
    llm = _repair_llm("42")
    sch = Scheduler(plan=plan, executor=ex, code_repair_llm=llm,
                    node_repair_max_retries=0)
    result = await _run_with_drive(sch)
    assert result.node_states["n1"] == ExecutionState.FAILED
    assert llm.chat.call_count == 0

    # ② SANDBOX_TIMEOUT 非代码错误（重跑也会再超时）——不自愈
    n2 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={"code": "while True: pass"}, depends_on=[])
    plan2 = DAGPlan(plan_id="p2", task_id="t", session_id="s",
                    nodes=[n2], entry_node_ids=["n1"])
    ex2 = FlakyExecutor(fail_first=1, error_code="SANDBOX_TIMEOUT")
    sch2 = Scheduler(plan=plan2, executor=ex2, code_repair_llm=_repair_llm("42"))
    result2 = await _run_with_drive(sch2)
    assert result2.node_states["n1"] == ExecutionState.FAILED

    # ③ code_repair_llm 缺失（引擎未注入 LLM）——不自愈
    ex3 = FlakyExecutor(fail_first=1)
    sch3 = Scheduler(plan=DAGPlan(plan_id="p3", task_id="t", session_id="s",
                                  nodes=[DAGNode(
                                      node_id="n1", kind="tool",
                                      tool_name="run_python",
                                      inputs={"code": "x ="})],
                                  entry_node_ids=["n1"]),
                     executor=ex3)
    result3 = await _run_with_drive(sch3)
    assert result3.node_states["n1"] == ExecutionState.FAILED
