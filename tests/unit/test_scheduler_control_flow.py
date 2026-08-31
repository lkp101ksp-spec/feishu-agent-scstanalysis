"""Phase 13 T3 控制流解释器单测：branch / for / while 展开 + 判定容错。

FakeLLM 预置判定序列；AutoFinishExecutor submit 即 SUCCESS（控制流在
调度循环内同步展开，工具节点即时完成即可驱动全链路）。
"""
import asyncio
import datetime as dt

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import (
    _CONTEXT_MAX_CHARS,
    Scheduler,
    _ask_bool,
)
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


class FakeLLM:
    """预置判定序列；元素为 Exception 时抛出。"""

    def __init__(self, seq=None):
        self._seq = list(seq or [])
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if not self._seq:
            raise RuntimeError("no more canned answers")
        v = self._seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


class AutoFinishExecutor:
    """submit 即 SUCCESS：outputs 按 tool_name 预置。"""

    def __init__(self, outputs_for=None):
        self.submitted: list[ExecutionTask] = []
        self.outputs_for = outputs_for or {}

    def submit(self, task: ExecutionTask) -> TaskHandle:
        self.submitted.append(task)
        return TaskHandle(
            execution_id=f"e{len(self.submitted)}", task_id=task.task_id,
            node_id=task.node_id, state=ExecutionState.SUCCESS,
            started_at=dt.datetime.now(dt.UTC), finished_at=dt.datetime.now(dt.UTC),
            outputs=dict(self.outputs_for.get(task.tool_name, {"result": "ok"})),
        )

    def get_status(self, handle):
        return handle.state

    def cancel(self, handle):
        handle.state = ExecutionState.CANCELLED

    def list_active(self):
        return []


async def _run(sch: Scheduler, timeout=2.0):
    """在当前事件循环内驱动调度（pytest-asyncio auto 模式）。"""
    return await asyncio.wait_for(sch.run_until_done(), timeout=timeout)


def _branch_plan():
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="read_doc",
                 inputs={}, depends_on=[])
    b1 = DAGNode(
        node_id="b1", kind="branch", condition_prompt="文档是否超过 500 字",
        depends_on=["n1"],
        true_branch=[DAGNode(node_id="t1", kind="tool", tool_name="summarize_text",
                             inputs={"text": "n1.text"}, depends_on=[])],
        false_branch=[DAGNode(node_id="f1", kind="tool", tool_name="classify_intent",
                              inputs={"text": "short"}, depends_on=[])],
    )
    return DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, b1], entry_node_ids=["n1"]), b1


async def test_branch_true_expands_true_branch():
    """判定 true → 展开 true 分支副本执行，false 分支不出现。"""
    plan, _ = _branch_plan()
    llm = FakeLLM(seq=["true"])
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.status == "success"
    assert result.node_states["b1_tt1"] == ExecutionState.SUCCESS
    assert "b1_ff1" not in result.node_states
    assert sch._handles["b1"].outputs == {"chosen": "true_branch"}
    assert len(llm.calls) == 1


async def test_branch_false_expands_false_branch():
    """判定 false（中文「否」）→ 展开 false 分支。"""
    plan, _ = _branch_plan()
    llm = FakeLLM(seq=["否"])
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.node_states["b1_ff1"] == ExecutionState.SUCCESS
    assert "b1_tt1" not in result.node_states
    assert sch._handles["b1"].outputs == {"chosen": "false_branch"}


async def test_branch_judge_exception_fails_node():
    """LLM 判定异常 → branch FAILED(LLM_FAILED)，展开的下游无节点。"""
    plan, _ = _branch_plan()
    llm = FakeLLM(seq=[RuntimeError("llm down")])
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    h = sch._handles["b1"]
    assert h.state == ExecutionState.FAILED
    assert h.error_code == "LLM_FAILED"
    assert result.status == "failed"


async def test_branch_without_condition_llm_fails():
    """判定器未注入（condition_llm=None）→ FAILED（不静默走分支）。"""
    plan, _ = _branch_plan()
    sch = Scheduler(plan=plan, executor=AutoFinishExecutor())
    result = await _run(sch)
    assert sch._handles["b1"].error_code == "LLM_FAILED"
    assert result.status == "failed"


def test_ask_bool_wording_tolerance():
    """判定输出措辞容错：true/false/是/否/成立。"""
    llm = FakeLLM(seq=["true", "True.", "是", "false", "不成立"])
    assert _ask_bool(llm, "c", "x") is True
    assert _ask_bool(llm, "c", "x") is True
    assert _ask_bool(llm, "c", "x") is True
    assert _ask_bool(llm, "c", "x") is False
    assert _ask_bool(llm, "c", "x") is False
    llm2 = FakeLLM(seq=["无法判断"])
    assert _ask_bool(llm2, "c", "x") is None


def test_upstream_context_big_field_does_not_drown_text():
    """巨大字段（block_tree）不淹没 text：分字段格式化 + 长度元数据。

    真机 2026-08-30：整体 JSON 截断后判定「文档是否超 500 字」时
    text 根本不在上下文里，LLM 只能瞎猜走了 false_branch。
    """
    plan, _ = _branch_plan()
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=FakeLLM())
    big_tree = [{"block_id": f"blk{i:03d}"} for i in range(200)]  # 远超 2000 字符
    long_text = "字" * 1234
    sch._handles["n1"] = TaskHandle(
        execution_id="e0", task_id="t", node_id="n1",
        state=ExecutionState.SUCCESS,
        started_at=dt.datetime.now(dt.UTC), finished_at=dt.datetime.now(dt.UTC),
        outputs={"block_tree": big_tree, "text": long_text},
    )
    ctx = sch._upstream_context(sch._node_map["b1"])
    # text 字段可见且带总长度元数据（判定长度条件的依据）
    assert "text<共1234字符>" in ctx
    assert ctx.index("text<共1234字符>") < _CONTEXT_MAX_CHARS


def _for_plan(max_iterations=100, items=None):
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="gen", inputs={}, depends_on=[])
    body = [
        DAGNode(node_id="s1", kind="tool", tool_name="summarize_text",
                inputs={"text": "{item}"}, depends_on=["f1"]),
        DAGNode(node_id="s2", kind="tool", tool_name="classify_intent",
                inputs={"text": "{item}"}, depends_on=["s1"]),
    ]
    f1 = DAGNode(node_id="f1", kind="for", iterate_over="n1.records",
                 depends_on=["n1"], body=body, max_iterations=max_iterations)
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, f1], entry_node_ids=["n1"])
    ex = AutoFinishExecutor(outputs_for={"gen": {"records": items or []}})
    return plan, ex


async def test_for_expands_each_item_with_substitution():
    """每项展开 body 副本：str 项原样替换、dict 项 JSON 替换；依赖重写生效。"""
    plan, ex = _for_plan(items=["alpha", {"k": "v"}])
    sch = Scheduler(plan=plan, executor=ex, condition_llm=FakeLLM())
    result = await _run(sch)
    assert result.status == "success"
    # 两份副本 × body 两个节点
    texts = [t.inputs["text"] for t in ex.submitted if t.tool_name == "summarize_text"]
    assert texts == ["alpha", '{"k": "v"}']
    # 依赖重写：s1 原依赖 f1（控制流节点）→ 继承 n1；s2 依赖 s1 → 加前缀
    s1_first = next(t for t in ex.submitted if t.node_id == "f1_i0_s1")
    assert s1_first.inputs["text"] == "alpha"
    assert sch._node_map["f1_i0_s2"].depends_on == ["f1_i0_s1"]
    assert sch._handles["f1"].outputs == {"iterations": 2}


async def test_for_invalid_iterate_over_fails():
    """iterate_over 指向非 list 输出 → FAILED(INVALID_INPUT)。"""
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s", nodes=[
        DAGNode(node_id="n1", kind="tool", tool_name="gen", inputs={}, depends_on=[]),
        DAGNode(node_id="f1", kind="for", iterate_over="n1.text",
                depends_on=["n1"],
                body=[DAGNode(node_id="s1", kind="tool", tool_name="x",
                              inputs={}, depends_on=[])]),
    ], entry_node_ids=["n1"])
    ex = AutoFinishExecutor(outputs_for={"gen": {"text": "not a list"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=FakeLLM())
    result = await _run(sch)
    assert sch._handles["f1"].error_code == "INVALID_INPUT"
    assert result.status == "failed"


async def test_for_truncates_over_max_iterations():
    """项数超 max_iterations → 截断 + outputs.iterations=max。"""
    plan, ex = _for_plan(max_iterations=2, items=["a", "b", "c"])
    sch = Scheduler(plan=plan, executor=ex, condition_llm=FakeLLM())
    await _run(sch)
    assert sch._handles["f1"].outputs == {"iterations": 2}
    ids = {t.node_id for t in ex.submitted}
    assert "f1_i2_s1" not in ids  # 第 3 项被截断


def _while_plan(max_iterations=10, body_fail=False):
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="gen", inputs={}, depends_on=[])
    body = [DAGNode(node_id="c1", kind="tool", tool_name="run_python",
                    inputs={"code": "import random\nrandom.random()"},
                    depends_on=["w1"])]
    # 注意：这里刻意用非数值表述走 LLM 判定路径（规则短路另有专门用例）
    w1 = DAGNode(node_id="w1", kind="while",
                 while_condition_prompt="c1.result 是否已达到目标",
                 depends_on=["n1"], body=body, max_iterations=max_iterations)
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, w1], entry_node_ids=["n1"])
    return plan, body_fail


async def test_while_runs_rounds_until_false():
    """判定 true,true,false → 2 轮 body 执行后 SUCCESS(rounds=2)。"""
    plan, _ = _while_plan()
    llm = FakeLLM(seq=["true", "true", "false"])
    ex = AutoFinishExecutor(outputs_for={"run_python": {"result": "0.5"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.status == "success"
    assert sch._handles["w1"].outputs == {"rounds": 2}
    assert result.node_states["w1_r0_c1"] == ExecutionState.SUCCESS
    assert result.node_states["w1_r1_c1"] == ExecutionState.SUCCESS
    assert len(llm.calls) == 3
    # while 清理退出状态
    assert "w1" not in sch._while_state


async def test_while_max_iterations_fails():
    """判定持续 true 且达 max_iterations → FAILED(LOOP_MAX_ITER)。"""
    plan, _ = _while_plan(max_iterations=2)
    llm = FakeLLM(seq=["true", "true", "true"])
    ex = AutoFinishExecutor(outputs_for={"run_python": {"result": "0.1"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    h = sch._handles["w1"]
    assert h.state == ExecutionState.FAILED
    assert h.error_code == "LOOP_MAX_ITER"
    assert result.status == "failed"


async def test_while_body_dependency_inherits_upstream():
    """body 子节点 depends_on 指向 while 自身 → 副本继承 while 的上游依赖。"""
    plan, _ = _while_plan()
    llm = FakeLLM(seq=["false"])  # 首轮即 false，不展开
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.status == "success"
    assert sch._handles["w1"].outputs == {"rounds": 0}


async def test_while_no_upstream_runs_first_round_without_judging():
    """无上游数据 → do-while：首轮免判定直接执行（「直到…为止」类任务）。

    真机 2026-08-30：w1 depends_on=[] 且条件引用 body 输出，首轮上下文
    为空 → LLM 无据判 false → 0 轮退出。
    第 2 轮判定（0.997 < 0.99 = False）被规则短路，全程不调 LLM。
    """
    body = [DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                    inputs={"code": "import random\nrandom.random()"},
                    depends_on=[])]
    w1 = DAGNode(node_id="w1", kind="while",
                 while_condition_prompt="n1.result 是否小于 0.99",
                 depends_on=[], body=body, max_iterations=20)
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[w1], entry_node_ids=["w1"])
    llm = FakeLLM(seq=["false"])  # 若误走 LLM 首轮即消耗并改变轮数
    ex = AutoFinishExecutor(outputs_for={"run_python": {"result": "0.997"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.status == "success"
    assert sch._handles["w1"].outputs == {"rounds": 1}
    assert result.node_states["w1_r0_n1"] == ExecutionState.SUCCESS
    assert len(llm.calls) == 0  # 首轮免判定 + 第 2 轮规则短路


async def test_while_condition_prompt_refs_rewritten_to_round_copies():
    """判定 prompt 引用 body 原始 id → 重写为当前轮副本 id（名字对齐上下文）。

    真机 2026-08-30：模型判据写 n_step.result 而上下文是
    w1_r0_s1.result，错位致 LLM 判定失据。
    """
    plan, _ = _while_plan()
    llm = FakeLLM(seq=["true", "false"])
    ex = AutoFinishExecutor(outputs_for={"run_python": {"result": "0.5"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    assert result.status == "success"
    assert sch._handles["w1"].outputs == {"rounds": 1}
    # 第二次判定（round 1）的 prompt 里 n_step 应已被重写为 w1_r0_c1
    second_call = llm.calls[1][1].content  # [1] 为 user 消息
    assert "w1_r0_c1.result" in second_call
    assert "n_step.result" not in second_call


async def test_branch_nested_while_control_fields_preserved():
    """branch 分支内嵌 while：副本须带控制流字段（否则缺条件 prompt 判定失败）。

    真机 2026-08-30：b1_fw1 副本丢 while_condition_prompt → LLM_FAILED。
    """
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="run_python",
                 inputs={"code": "0.5"}, depends_on=[])
    w1 = DAGNode(
        node_id="w1", kind="while",
        while_condition_prompt="上轮随机数是否小于 0.99",
        depends_on=["n1"], max_iterations=3,
        body=[DAGNode(node_id="c1", kind="tool", tool_name="run_python",
                      inputs={"code": "import random\nrandom.random()"},
                      depends_on=["w1"])],
    )
    b1 = DAGNode(node_id="b1", kind="branch",
                 condition_prompt="n1.result 是否大于等于 0.99",
                 depends_on=["n1"], true_branch=[], false_branch=[w1])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, b1], entry_node_ids=["n1"])
    # 判定序列：b1 → false（进 while 分支）；w1 副本首轮 → false（即退出）
    llm = FakeLLM(seq=["false", "false"])
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    # 嵌套 while 副本正常判定（SUCCESS rounds=0），而非 LLM_FAILED
    assert sch._handles["b1_fw1"].state == ExecutionState.SUCCESS
    assert sch._handles["b1_fw1"].outputs == {"rounds": 0}
    assert result.status == "success"


# === Phase 14 后续优化：判定规则短路（纯数值比较不走 LLM） ===

def test_rule_evaluate_direct():
    """可短路：单一 ref op number 且上下文值可数值化；否则 None 回退 LLM。"""
    from orchestrator.planner.scheduler import _rule_evaluate
    assert _rule_evaluate("c1.result < 0.99", "c1.result: 0.5") is True
    assert _rule_evaluate("c1.result < 0.99", "c1.result: 0.995") is False
    assert _rule_evaluate("c1.result 是否大于 3", "c1.result: 5") is True
    # 长度元数据行（"ref<共N字符>: 前缀…"）也能取值
    assert _rule_evaluate("c1.result >= 1.5", "c1.result<共20字符>: 1.5") is True
    # 三段引用（综合演练轮）：前缀行容器值取子字段，JSON/repr 双格式
    assert _rule_evaluate(
        "n2.result.retrieved >= 5",
        'n2.result: {"retrieved": 5, "total": 67407}') is True
    assert _rule_evaluate(
        "n2.result.percentage > 0.05",
        "n2.result: {'retrieved': 5, 'percentage': 0.01}") is False
    assert _rule_evaluate(
        "n2.result.retrieved >= 5", "n2.result: abc") is None  # 值非容器
    assert _rule_evaluate(
        "n2.result.retrieved >= 5", "n2.other: 1") is None  # 前缀行不存在
    # 以下均不可短路 → None（回退 LLM 判定）
    assert _rule_evaluate("a.x < 1 且 b.y > 2", "a.x: 0") is None  # 多条件
    assert _rule_evaluate("c1.result < 0.99", "c1.result: abc") is None  # 值非数值
    assert _rule_evaluate("c1.result < 0.99", "other: 1") is None  # 上下文无该引用
    assert _rule_evaluate("文档是否超过 500 字", "") is None  # 无 ref.field
    assert _rule_evaluate("c1.result 是否已达到目标", "c1.result: 0.5") is None


async def test_while_rule_shortcut_skips_llm():
    """规则可判的 while 全程不调 LLM：首轮 do-while 执行 + 第 2 轮规则判定停。"""
    body = [DAGNode(node_id="c1", kind="tool", tool_name="run_python",
                    inputs={"code": "0.995"}, depends_on=[])]
    w1 = DAGNode(node_id="w1", kind="while",
                 while_condition_prompt="c1.result < 0.99",
                 depends_on=[], body=body, max_iterations=5)
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[w1], entry_node_ids=["w1"])
    llm = FakeLLM(seq=["true"])  # 若误走 LLM 会改变轮数，断言即失败
    ex = AutoFinishExecutor(outputs_for={"run_python": {"result": "0.995"}})
    sch = Scheduler(plan=plan, executor=ex, condition_llm=llm)
    result = await _run(sch)
    # 首轮执行后规则判定 0.995 < 0.99 为 False → 收敛停止，全程 0 次 LLM
    assert result.status == "success"
    assert sch._handles["w1"].outputs == {"rounds": 1}
    assert len(llm.calls) == 0


# === Phase 17：节点级 L2 审批门（l2_gate） ===

def _l2_plan():
    """n1(读) → n3(write_doc) 链；write_doc 依赖 n1。"""
    n1 = DAGNode(node_id="n1", kind="tool", tool_name="read_doc",
                 inputs={}, depends_on=[])
    n3 = DAGNode(node_id="n3", kind="tool", tool_name="write_doc",
                 inputs={"doc_id": "doc1"}, depends_on=["n1"])
    return DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n1, n3], entry_node_ids=["n1"])


async def test_l2_gate_deny_marks_denied_and_skips_downstream():
    """gate 拒绝 write_doc → 节点 DENIED + 下游 SKIPPED + plan partial。"""
    plan = _l2_plan()
    ex = AutoFinishExecutor()
    gate_calls = []

    def gate(node, inputs):
        gate_calls.append(node.node_id)
        if node.tool_name == "write_doc":
            return False, "TOOL_DENIED"
        return True, ""

    sch = Scheduler(plan=plan, executor=ex, l2_gate=gate)
    result = await _run(sch)
    assert result.node_states["n1"] == ExecutionState.SUCCESS
    assert result.node_states["n3"] == ExecutionState.DENIED
    assert sch._handles["n3"].error_code == "TOOL_DENIED"
    # write_doc 未进 executor
    assert [t.node_id for t in ex.submitted] == ["n1"]
    # DENIED 计入 partial（无 FAILED 无 SKIPPED 但有 DENIED）
    assert result.status == "success_with_partial_failure"


async def test_l2_gate_approve_passes_all():
    """gate 全放行 → 两节点均提交执行，plan success。"""
    plan = _l2_plan()
    ex = AutoFinishExecutor()

    sch = Scheduler(plan=plan, executor=ex, l2_gate=lambda n, i: (True, ""))
    result = await _run(sch)
    assert [t.node_id for t in ex.submitted] == ["n1", "n3"]
    assert result.status == "success"
    assert result.node_states["n3"] == ExecutionState.SUCCESS


async def test_no_gate_keeps_behavior():
    """未注入 gate → 行为与原先一致（无 DENIED，全部提交）。"""
    plan = _l2_plan()
    ex = AutoFinishExecutor()
    sch = Scheduler(plan=plan, executor=ex)
    result = await _run(sch)
    assert [t.node_id for t in ex.submitted] == ["n1", "n3"]
    assert result.status == "success"
