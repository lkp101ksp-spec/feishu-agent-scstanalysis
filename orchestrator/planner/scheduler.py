"""DAG Scheduler：协程轮询驱动 ready 节点 → ExecutorClient。

- ready = 所有 depends_on 已 SUCCESS
- 失败处理：on_node_fail=continue → 下游 SKIPPED；stop_plan → 终止
- 并发上限：max_concurrent_nodes（默认 4）
- 默认 on_node_fail=continue（见 ADR）

Phase 2 实现：
- run_until_done() 用 asyncio.sleep(0.01) 轮询 + 触发新节点
- 节点状态变化由 ExecutorClient.get_status() 反馈
- 全部终态后 collect_result() 汇总 PlanResult

Phase 13 T3：控制流解释器——branch/for/while 节点在调度循环内同步解释
（LLM 判定 / 展开 body 副本追加进 plan.nodes），详见 spec
2026-08-30-phase13-t3-control-flow-design。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)

# 引用字段缺失时的兜底别名（按优先级）——模型猜输出字段名不可能全对
# （真机 2026-08-30：blast 输出 records 被模型猜成 summary）
_FIELD_ALIASES = ("records", "text", "results", "summary")

# 控制流节点的 LLM 条件判定角色词（只输出 true/false）
_JUDGE_SYSTEM = (
    "你是条件判定器。根据给定上下文判断条件是否成立，"
    "只输出 true 或 false（或 是/否），不要输出任何其他内容。"
)

# 判定上下文/输出摘要截断（防 prompt 膨胀）
_CONTEXT_MAX_CHARS = 2000

# 内嵌引用 token：<node_id>.<field>（id 须是合法标识符）
_REF_TOKEN_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")

_TERMINAL_STATES = (
    ExecutionState.SUCCESS,
    ExecutionState.FAILED,
    ExecutionState.DENIED,
    ExecutionState.SKIPPED,
    ExecutionState.CANCELLED,
)


def _ask_bool(llm, condition: str, context: str) -> Optional[bool]:
    """LLM 判定自然语言条件：true/false（中英文措辞均容错）。

    返回 None 表示不可判定（LLM 异常由调用方捕获 / 输出无法解析）。
    """
    from shared.schemas import ChatMessage

    resp = llm.chat([
        ChatMessage(role="system", content=_JUDGE_SYSTEM),
        ChatMessage(
            role="user",
            content=f"条件：{condition}\n\n上下文：\n{context or '（无）'}",
        ),
    ])
    text = str(resp).strip().lower()
    # 负向词优先——「不成立」含正词「成立」，须先判否定
    if any(w in text for w in ("false", "not true", "no", "否", "不成立", "错", "不满足")):
        return False
    if any(w in text for w in ("true", "yes", "是", "成立", "对", "满足")):
        return True
    return None


@dataclass
class PlanResult:
    plan_id: str
    status: str  # success | success_with_partial_failure | failed
    node_states: dict[str, ExecutionState]
    started_at: datetime
    finished_at: datetime


class Scheduler:
    def __init__(self, plan: DAGPlan, executor, max_concurrent: int = 4,
                 runtime=None, condition_llm=None) -> None:
        self.plan = plan
        self.executor = executor
        self.max_concurrent = max_concurrent
        self.runtime = runtime  # Phase 3：可选注入；None 时走 Phase 2 路径
        # T3：branch/while 条件判定器（llm_router）；None 时控制流节点 FAILED
        self._condition_llm = condition_llm
        # T3：while 重入状态 node_id -> {"round": 已展开轮数, "body_ids": [...]}}
        self._while_state: dict[str, dict] = {}
        self._handles: dict[str, TaskHandle] = {}
        self._node_map: dict[str, DAGNode] = {n.node_id: n for n in plan.nodes}
        self._started_at = datetime.utcnow()

    @staticmethod
    def _expand_subplan_static(node: DAGNode, template_service) -> list[DAGNode]:
        """Phase 5: 展开 sub-Plan 模板为内联 DAGNode 列表。"""
        if not node.subplan_template_id:
            return [node]
        tpl = template_service.get(node.subplan_template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {node.subplan_template_id} not found")
        if tpl.type != "subplan":
            raise ValueError(
                f"template {node.subplan_template_id} is not subplan"
            )
        steps = template_service.render_subplan(
            template_id=node.subplan_template_id,
            params=node.subplan_params,
        )
        out = []
        for i, step in enumerate(steps):
            out.append(DAGNode(
                node_id=f"{node.node_id}_step_{i}",
                kind="tool",
                tool_name=step.tool_name,
                inputs=step.inputs,
                depends_on=list(node.depends_on) if i == 0 else [],
            ))
        return out

    def _node_state(self, node_id: str) -> ExecutionState:
        if node_id not in self._handles:
            return ExecutionState.PENDING
        return self._handles[node_id].state

    def _ready_nodes(self) -> list[DAGNode]:
        out: list[DAGNode] = []
        for node in self.plan.nodes:
            if node.node_id in self._handles:
                continue
            # T3 while 重入门控：已展开轮的 body 未全部终态前不得再次判定
            if node.kind == "while":
                ws = self._while_state.get(node.node_id)
                if ws and not self._body_all_terminal(ws["body_ids"]):
                    continue
            upstreams = [self._node_state(d) for d in node.depends_on]
            if not upstreams:
                out.append(node)
                continue
            # 上游任一非 SUCCESS → 下游 SKIPPED
            bad = any(
                s in (
                    ExecutionState.FAILED,
                    ExecutionState.DENIED,
                    ExecutionState.SKIPPED,
                    ExecutionState.CANCELLED,
                )
                for s in upstreams
            )
            if bad:
                self._handles[node.node_id] = TaskHandle(
                    execution_id=f"sk_{node.node_id}",
                    task_id=self.plan.task_id,
                    node_id=node.node_id,
                    state=ExecutionState.SKIPPED,
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
                continue
            if all(s == ExecutionState.SUCCESS for s in upstreams):
                out.append(node)
        return out

    def _all_terminal(self) -> bool:
        states = [self._node_state(n.node_id) for n in self.plan.nodes]
        return all(
            s in (
                ExecutionState.SUCCESS,
                ExecutionState.FAILED,
                ExecutionState.SKIPPED,
                ExecutionState.CANCELLED,
                ExecutionState.DENIED,
            )
            for s in states
        )

    def _resolve_inputs(self, node: DAGNode) -> dict:
        """从上游 outputs 解析 <node>.field 形式的引用（值非 str 时原样透传）。

        两级语义：
        1. 整值引用（"n1.text"）→ 替换为上游原始值（下游工具拿纯数据）；
        2. 字符串内嵌引用（code 里 len('n1.text')）→ 替换为 repr(值)
           （合法 Python 字面量）——模型自然期望引用在代码内生效
           （真机 2026-08-30：len('n1.text') 算了字面量长度 7）。
        仅当 `.` 前部分是计划内已知 node_id 时才视为引用——否则 code 等
        含 `.` 的普通字符串（如 f"{x:.6e}"、np.arange）原样保留
        （真机 2026-08-30：run_python 的 code 含点→None→空文件假 success）。
        整值引用字段缺失时按别名兜底（records/text/results/summary）——
        模型猜错字段名不应导致下游拿到 None（真机 2026-08-30）。
        """
        resolved: dict = {}
        for k, v in node.inputs.items():
            if isinstance(v, str) and "." in v:
                upstream_id, field_name = v.split(".", 1)
                if upstream_id not in self._node_map:
                    # 非整值引用 → 尝试内嵌引用替换（code 内插）
                    resolved[k] = self._inline_substitute(v)
                    continue
                up_handle = self._handles.get(upstream_id)
                if up_handle and up_handle.outputs:
                    if field_name in up_handle.outputs:
                        resolved[k] = up_handle.outputs[field_name]
                        continue
                    for alias in _FIELD_ALIASES:
                        if alias in up_handle.outputs:
                            logger.warning(
                                "node %s 引用 %s.%s 不存在，回退 %s.%s",
                                node.node_id, upstream_id, field_name,
                                upstream_id, alias,
                            )
                            resolved[k] = up_handle.outputs[alias]
                            break
                    else:
                        logger.warning(
                            "node %s 引用 %s.%s 不存在且无别名可回退",
                            node.node_id, upstream_id, field_name,
                        )
                        resolved[k] = None
                else:
                    resolved[k] = None
            else:
                resolved[k] = v
        return resolved

    def _inline_substitute(self, text: str) -> str:
        """字符串内嵌的 <node>.<field> 替换为 Python 字面量。

        严格限定：id 是计划内节点、上游有输出、字段存在——普通代码
        （np.arange / e.g. / self.x）不受影响。
        引用已被引号包裹（'n1.text'）时只替换内核（外层引号保留），
        否则整段 repr——否则 ''xxx'' 双层引号是语法错误。
        """
        def _sub(m: "re.Match") -> str:
            up_id, field = m.group(1), m.group(2)
            h = self._handles.get(up_id)
            if h is None or not h.outputs or field not in h.outputs:
                return m.group(0)
            r = repr(h.outputs[field])
            prev = text[m.start() - 1] if m.start() > 0 else ""
            nxt = text[m.end()] if m.end() < len(text) else ""
            if prev in ("'", '"') and nxt in ("'", '"'):
                return r[1:-1]
            return r

        return _REF_TOKEN_RE.sub(_sub, text)

    def _refresh_running_handles(self) -> None:
        for h in list(self._handles.values()):
            if h.state == ExecutionState.RUNNING:
                current = self.executor.get_status(h)
                if current != ExecutionState.RUNNING:
                    h.state = current

    async def run_until_done(self) -> PlanResult:
        # Phase 3：runtime 注入时委托给 Runtime（dynamic-append / loop / freeze）
        if self.runtime is not None:
            from orchestrator.runtime.plan_runtime import RuntimeState
            self.runtime.state = RuntimeState.RUNNING
            try:
                # Phase 3 简化版：Runtime 仅承载状态；驱动循环仍由 Scheduler 负责
                # 完整调度集成由 Phase 3.1 / Phase 4 完成
                return await self._run_phase2_loop()
            finally:
                self.runtime.state = RuntimeState.TERMINAL
        return await self._run_phase2_loop()

    async def _run_phase2_loop(self) -> PlanResult:
        # Phase 2 原逻辑（保留）+ T3 控制流分流
        while not self._all_terminal():
            ready = self._ready_nodes()
            for node in ready[: self.max_concurrent]:
                if node.kind in ("branch", "while", "for"):
                    self._expand_control_node(node)
                    continue
                task = ExecutionTask(
                    task_id=self.plan.task_id,
                    node_id=node.node_id,
                    tool_name=node.tool_name or "",
                    inputs=self._resolve_inputs(node),
                )
                handle = self.executor.submit(task)
                self._handles[node.node_id] = handle
            await asyncio.sleep(0.01)
            self._refresh_running_handles()
        return self._collect_result()

    def _collect_result(self) -> PlanResult:
        node_states = {n.node_id: self._node_state(n.node_id) for n in self.plan.nodes}
        failed = any(s == ExecutionState.FAILED for s in node_states.values())
        skipped = any(s == ExecutionState.SKIPPED for s in node_states.values())
        if failed and not skipped:
            status = "failed"
        elif failed or skipped:
            status = "success_with_partial_failure"
        else:
            status = "success"
        return PlanResult(
            plan_id=self.plan.plan_id,
            status=status,
            node_states=node_states,
            started_at=self._started_at,
            finished_at=datetime.utcnow(),
        )

    # === Phase 13 T3：控制流解释器（branch / for / while）===

    def _expand_control_node(self, node: DAGNode) -> None:
        """控制流节点同步解释：不进 executor，展开副本由轮询循环调度。"""
        if node.kind == "branch":
            self._expand_branch(node)
        elif node.kind == "for":
            self._expand_for(node)
        elif node.kind == "while":
            self._expand_while(node)

    def _expand_branch(self, node: DAGNode) -> None:
        """LLM 判定 condition_prompt → 展开选中分支（空分支跳过）。"""
        cond = self._eval_condition(node, node.condition_prompt or "",
                                    self._upstream_context(node))
        if cond is None:
            self._fail_control(node, "LLM_FAILED",
                               f"branch 条件判定失败（condition_prompt="
                               f"{node.condition_prompt!r}）")
            return
        chosen = "true_branch" if cond else "false_branch"
        subs = node.true_branch if cond else (node.false_branch or [])
        prefix = f"{node.node_id}_{'t' if cond else 'f'}"
        self._install_copies(
            self._copy_subtree(subs, prefix=prefix, parent_node=node))
        self._succeed_control(node, {"chosen": chosen})

    def _expand_for(self, node: DAGNode) -> None:
        """iterate_over 解析上游 list → 每项展开 body 副本（{var} 字面替换）。"""
        items = self._resolve_iterate_over(node)
        if items is None:
            self._fail_control(node, "INVALID_INPUT",
                               f"for iterate_over 无效或非列表：{node.iterate_over!r}")
            return
        if len(items) > node.max_iterations:
            logger.warning(
                "for %s 项数 %d 超 max_iterations=%d，截断",
                node.node_id, len(items), node.max_iterations,
            )
            items = items[: node.max_iterations]
        placeholder = "{%s}" % node.iteration_var
        for i, item in enumerate(items):
            copies = self._copy_subtree(
                node.body, prefix=f"{node.node_id}_i{i}_",
                parent_node=node, placeholder=placeholder, value=item,
            )
            self._install_copies(copies)
        self._succeed_control(node, {"iterations": len(items)})

    def _expand_while(self, node: DAGNode) -> None:
        """重入式 while：每轮 body 终态后重新 LLM 判定。

        首轮上下文=直接上游摘要；后续轮=上一轮 body 输出摘要（含失败信息）。
        判定 false → SUCCESS；true 且已达 max_iterations → FAILED。
        """
        ws = self._while_state.get(node.node_id, {"round": 0, "body_ids": []})
        context = (self._upstream_context(node) if ws["round"] == 0
                   else self._body_context(ws["body_ids"]))
        cond = self._eval_condition(node, node.while_condition_prompt or "", context)
        if cond is None:
            self._fail_control(node, "LLM_FAILED",
                               f"while 条件判定失败（round={ws['round']}）")
            return
        if not cond:
            self._while_state.pop(node.node_id, None)
            self._succeed_control(node, {"rounds": ws["round"]})
            return
        if ws["round"] >= node.max_iterations:
            self._fail_control(node, "LOOP_MAX_ITER",
                               f"while 超 max_iterations={node.max_iterations}")
            return
        copies = self._copy_subtree(
            node.body, prefix=f"{node.node_id}_r{ws['round']}_", parent_node=node)
        self._install_copies(copies)
        self._while_state[node.node_id] = {
            "round": ws["round"] + 1,
            "body_ids": [c.node_id for c in copies],
        }

    def _copy_subtree(self, nodes: list[DAGNode], *, prefix: str,
                      parent_node: DAGNode,
                      placeholder: str | None = None, value=None) -> list[DAGNode]:
        """子树副本：id 加前缀 + 依赖重写 + 占位符替换。

        依赖重写：指向父控制流节点 → 继承其 depends_on；子树内互链 → 加前缀；
        其他外部依赖原样保留（validate_dag 已保证存在）。
        控制流字段（condition_prompt/body 等）必须随副本传递——branch 分支
        内可嵌 while/for（validate_dag 只禁循环体互嵌），真机 2026-08-30：
        b1_fw1 副本丢 while_condition_prompt → 判定 LLM_FAILED。
        嵌套子数组引用原对象共享：其展开发生在该副本自身被解释时（再加
        自己的前缀），无需在此轮递归复制。
        """
        id_map = {n.node_id: prefix + n.node_id for n in nodes}
        subst = None
        if placeholder is not None:
            subst = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, default=str)
        out = []
        for n in nodes:
            deps: list[str] = []
            for d in n.depends_on:
                if d in id_map:
                    deps.append(id_map[d])
                elif d == parent_node.node_id:
                    deps.extend(parent_node.depends_on)
                else:
                    deps.append(d)
            inputs = dict(n.inputs)
            if subst is not None:
                inputs = {k: v.replace(placeholder, subst)
                          for k, v in inputs.items() if isinstance(v, str)}
            out.append(DAGNode(
                node_id=id_map[n.node_id], kind=n.kind, tool_name=n.tool_name,
                inputs=inputs, depends_on=deps, config=n.config,
                condition=n.condition, join_strategy=n.join_strategy,
                on_node_fail=n.on_node_fail,
                condition_prompt=n.condition_prompt,
                true_branch=n.true_branch, false_branch=n.false_branch,
                while_condition_prompt=n.while_condition_prompt,
                body=n.body, max_iterations=n.max_iterations,
                iterate_over=n.iterate_over, iteration_var=n.iteration_var,
                subplan_template_id=n.subplan_template_id,
                subplan_params=n.subplan_params,
            ))
        return out

    def _install_copies(self, copies: list[DAGNode]) -> None:
        """展开副本追加进 plan（轮询循环自然调度）。"""
        for c in copies:
            self.plan.nodes.append(c)
            self._node_map[c.node_id] = c

    def _resolve_iterate_over(self, node: DAGNode) -> Optional[list]:
        """解析 for.iterate_over（<node>.<field> 引用）为上游 list 输出。"""
        ref = node.iterate_over or ""
        if "." not in ref:
            return None
        up_id, field = ref.split(".", 1)
        if up_id not in self._node_map:
            return None
        h = self._handles.get(up_id)
        if h is None or not h.outputs or field not in h.outputs:
            return None
        val = h.outputs[field]
        return val if isinstance(val, list) else None

    def _eval_condition(self, node: DAGNode, prompt: str,
                        context: str) -> Optional[bool]:
        """LLM 条件判定；None = 不可判定（判定器未注入/异常/输出无法解析）。"""
        if self._condition_llm is None:
            logger.warning("node %s 控制流判定器未注入（condition_llm=None）",
                           node.node_id)
            return None
        if not prompt:
            logger.warning("node %s 控制流节点缺条件 prompt", node.node_id)
            return None
        try:
            return _ask_bool(self._condition_llm, prompt, context)
        except Exception as e:
            logger.warning("node %s LLM 判定异常: %s", node.node_id, e)
            return None

    def _upstream_context(self, node: DAGNode) -> str:
        """直接上游 outputs 摘要（控制流判定上下文）。

        按字段分别格式化并带长度元数据——整体 JSON 截断会让 block_tree
        等巨大字段淹没 text，判定「是否超 500 字」时 text 根本不在
        上下文里（真机 2026-08-30）。
        """
        parts: list[str] = []
        for dep in node.depends_on:
            h = self._handles.get(dep)
            if h is not None and h.outputs:
                fields = [self._format_field(k, v)
                          for k, v in h.outputs.items() if k != "ast_notices"]
                parts.append(f"{dep}: " + "\n".join(fields))
        return "\n".join(parts)[:_CONTEXT_MAX_CHARS]

    @staticmethod
    def _format_field(key: str, val, max_chars: int = 800) -> str:
        """字段摘要：带总长度元数据 + 截断预览（长度类条件判定依据）。"""
        sval = val if isinstance(val, str) else json.dumps(
            val, ensure_ascii=False, default=str)
        if len(sval) > max_chars:
            return f"{key}<共{len(sval)}字符>: {sval[:max_chars]}…"
        return f"{key}: {sval}"

    def _body_context(self, body_ids: list[str]) -> str:
        """while 上一轮 body 输出摘要（失败节点附错误信息，判定可见）。"""
        parts: list[str] = []
        for bid in body_ids:
            h = self._handles.get(bid)
            if h is None:
                continue
            if h.state == ExecutionState.FAILED:
                parts.append(f"{bid}: FAILED {h.error_code}: {h.error_message}")
            elif h.outputs:
                fields = [self._format_field(k, v)
                          for k, v in h.outputs.items() if k != "ast_notices"]
                parts.append(f"{bid}: " + "\n".join(fields))
        return "\n".join(parts)[:_CONTEXT_MAX_CHARS]

    def _body_all_terminal(self, body_ids: list[str]) -> bool:
        states = [self._node_state(b) for b in body_ids]
        return all(s in _TERMINAL_STATES for s in states)

    def _succeed_control(self, node: DAGNode, outputs: dict) -> None:
        """控制流节点置 SUCCESS（outputs 记展开元数据供 IM 展示轨迹）。"""
        self._handles[node.node_id] = TaskHandle(
            execution_id=f"cf_{node.node_id}_{new_ulid()}",
            task_id=self.plan.task_id, node_id=node.node_id,
            state=ExecutionState.SUCCESS,
            started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
            outputs=outputs,
        )

    def _fail_control(self, node: DAGNode, code: str, message: str) -> None:
        """控制流节点置 FAILED（LLM 判定/输入无效/超 max_iterations）。"""
        logger.warning("node %s (%s) failed: %s %s",
                       node.node_id, node.kind, code, message)
        self._handles[node.node_id] = TaskHandle(
            execution_id=f"cf_{node.node_id}_{new_ulid()}",
            task_id=self.plan.task_id, node_id=node.node_id,
            state=ExecutionState.FAILED,
            started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
            error_code=code, error_message=message,
        )
