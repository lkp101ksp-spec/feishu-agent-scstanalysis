"""自研 agentic loop：think→act→observe 多轮函数调用循环（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §4：
- 三重终止：max_steps(25) / token_budget(200k) / timeout_sec(3600)
- 连续 3 次工具失败 → 禁用工具再问一轮（逼模型纯文本收尾，仍调工具则 no_tools）
- 单条观察 >4k 字符截断；预算临界触发一次 compress_messages 历史压缩
- risk_map 标记的 L2 工具执行前必须 approve_fn 放行
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

OBS_TRUNCATE = 4000          # 单条观察字符上限
FAIL_DISABLE = 3             # 连续失败阈值
COMPRESS_KEEP_TAIL = 8       # 压缩时保留的最近消息条数


@dataclass
class LoopResult:
    """一次 loop 运行的结果。"""
    status: str                       # final/max_steps/budget_exhausted/timeout/no_tools
    final_text: str
    steps: int                        # 已消耗的 LLM 轮数
    approx_tokens: int
    abort_reason: str = ""
    tool_events: list[dict] = field(default_factory=list)  # {step,name,ok}
    # 连败禁用是否触发过（Phase 27 真机修复）：禁用后模型按引导文字收尾
    # 会得到 final 状态，诊断链依赖该标记区分"实质失败的 final"
    tools_disabled: bool = False


def truncate_observation(payload: dict, limit: int = OBS_TRUNCATE) -> str:
    """观察 dict → JSON 字符串，超限头尾保留截断。"""
    s = json.dumps(payload, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    head, tail = limit * 45 // 100, limit * 30 // 100
    return s[:head] + f"\n...[truncated {len(s) - head - tail} chars]...\n" + s[-tail:]


def compress_messages(messages: list[dict], keep_tail: int = COMPRESS_KEEP_TAIL) -> list[dict]:
    """历史压缩：system + 中段一行简报 + 最近 keep_tail 条。"""
    if len(messages) <= keep_tail + 1:
        return messages
    head = messages[0]
    mid = messages[1:-keep_tail]
    tail = messages[-keep_tail:]
    n_tools = sum(1 for m in mid if m.get("role") == "tool")
    digest = {"role": "user", "content":
              f"[上下文压缩] 此前 {len(mid)} 条消息（含 {n_tools} 次工具观察）已折叠。"}
    return [head, digest, *tail]


class AgentLoop:
    """多轮 function-calling 循环；llm 只需实现 chat_with_tools。"""

    def __init__(
        self,
        llm,
        tools_schema: list[dict],
        dispatch: Callable[[str, object], dict],
        *,
        max_steps: int = 25,
        token_budget: int = 200_000,
        timeout_sec: int = 3600,
        risk_map: Optional[dict[str, str]] = None,
        approve_fn: Optional[Callable[[list[str]], bool]] = None,
        on_step: Optional[Callable[[int, dict], None]] = None,
        model: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.tools_schema = tools_schema
        self.dispatch = dispatch
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.timeout_sec = timeout_sec
        self.risk_map = risk_map or {}
        self.approve_fn = approve_fn
        self.on_step = on_step
        self.model = model
        self.events: list[dict] = []   # tool_event 累积（随 LoopResult 返回）

    # ------------------------------------------------------------------ #
    def run(self, system: str, task: str) -> LoopResult:
        """执行循环直至终止；返回 LoopResult。"""
        started = time.monotonic()
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        failures = 0
        disabled = False
        compressed = False

        for step in range(1, self.max_steps + 1):
            if time.monotonic() - started >= self.timeout_sec:
                return LoopResult("timeout", "", step - 1,
                                  self._approx_tokens(messages), "timeout exceeded",
                                  self.events, disabled)

            resp = self.llm.chat_with_tools(
                messages, [] if disabled else self.tools_schema, model=self.model)
            content = (resp.get("content") or "").strip()
            calls = resp.get("tool_calls") or []

            if disabled and calls:
                return LoopResult("no_tools", content, step,
                                  self._approx_tokens(messages),
                                  "model kept requesting tools after disable",
                                  self.events, disabled)

            if not calls:
                self._emit(step, {"event": "final", "text": content[:200]})
                return LoopResult("final", content, step,
                                  self._approx_tokens(messages), "", self.events,
                                  disabled)

            messages.append({"role": "assistant", "content": resp.get("content") or "",
                             "tool_calls": calls})
            step_all_ok = True
            for tc in calls:
                name = tc["function"]["name"]
                arguments = tc["function"].get("arguments", "{}")
                obs = self._execute_tool(name, arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": truncate_observation(obs),
                })
                ok = bool(obs.get("ok"))
                self.events.append({"step": step, "name": name, "ok": ok})
                self._emit(step, {"event": "tool", "name": name, "ok": ok})
                if not ok:
                    step_all_ok = False

            failures = 0 if step_all_ok else failures + 1
            if failures >= FAIL_DISABLE and not disabled:
                disabled = True
                messages.append({"role": "user", "content":
                                 "工具已连续多次失败并被临时禁用，请直接用文字总结结论。"})
                self._emit(step, {"event": "tools_disabled"})

            # 预算：超限先压缩一次，仍超则优雅收尾
            if self._approx_tokens(messages) > self.token_budget:
                if not compressed and len(messages) > COMPRESS_KEEP_TAIL + 1:
                    messages = compress_messages(messages)
                    compressed = True
                    self._emit(step, {"event": "compressed", "n_msgs": len(messages)})
                if self._approx_tokens(messages) > self.token_budget:
                    return LoopResult("budget_exhausted", content or "", step,
                                      self._approx_tokens(messages),
                                      "token budget exceeded after compress",
                                      self.events, disabled)

        return LoopResult("max_steps", "", self.max_steps,
                          self._approx_tokens(messages), "max steps reached",
                          self.events, disabled)

    # ------------------------------------------------------------------ #
    def _execute_tool(self, name: str, arguments) -> dict:
        """L2 审批 → dispatch；异常与审批拒绝均转观察 dict。"""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return {"ok": False, "error": "BAD_ARGUMENTS_JSON", "tool": name}
        if self.risk_map.get(name, "L1").startswith("L2"):
            info = [f"skill_tool: {name}", str(arguments)[:200]]
            if self.approve_fn is None or not self.approve_fn(info):
                logger.info("L2 tool denied by user: %s", name)
                return {"ok": False, "error": "APPROVAL_DENIED", "tool": name}
        try:
            result = self.dispatch(name, arguments)
        except Exception as exc:  # noqa: BLE001 —— 观察必须回传而非崩溃
            logger.warning("dispatch %s raised: %s", name, exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return result if isinstance(result, dict) else {"ok": True, "result": result}

    def _approx_tokens(self, messages: list[dict]) -> int:
        """粗估 token：消息 JSON 字符数 / 4。"""
        return sum(len(json.dumps(m, ensure_ascii=False, default=str))
                   for m in messages) // 4

    def _emit(self, step: int, event: dict) -> None:
        """触发 on_step 回调（CodingRunner 用于过程反馈节流）。"""
        if self.on_step is not None:
            try:
                self.on_step(step, event)
            except Exception:  # noqa: BLE001 —— 回调绝不影响主流程
                logger.exception("on_step callback failed")
