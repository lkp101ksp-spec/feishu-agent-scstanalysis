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
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

OBS_TRUNCATE = 4000          # 单条观察字符上限
FAIL_DISABLE = 3             # 连续失败阈值
COMPRESS_KEEP_TAIL = 8       # 压缩时保留的最近消息条数
MEMORY_HEADER = "## 已试路径（避免重复尝试）"   # Working Memory 消息头（Phase 28 T2）
MEMORY_MAX_LINES = 5         # 失败记忆滚动窗口（最近 N 次失败）
MEMORY_OK_MAX_LINES = 3      # 成功记忆滚动窗口（Phase 29 T3：可复用结果）


@dataclass
class LoopResult:
    """一次 loop 运行的结果。"""
    status: str                       # final/max_steps/budget_exhausted/timeout/no_tools
    final_text: str
    steps: int                        # 已消耗的 LLM 轮数
    approx_tokens: int
    abort_reason: str = ""
    tool_events: list[dict[str, Any]] = field(default_factory=list)  # {step,name,ok}
    # 连败禁用是否触发过（Phase 27 真机修复）：禁用后模型按引导文字收尾
    # 会得到 final 状态，诊断链依赖该标记区分"实质失败的 final"
    tools_disabled: bool = False


def truncate_observation(payload: dict[str, Any], limit: int = OBS_TRUNCATE) -> str:
    """观察 dict → JSON 字符串，超限头尾保留截断。"""
    s = json.dumps(payload, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    head, tail = limit * 45 // 100, limit * 30 // 100
    return s[:head] + f"\n...[truncated {len(s) - head - tail} chars]...\n" + s[-tail:]


def compress_messages(
    messages: list[dict[str, Any]], keep_tail: int = COMPRESS_KEEP_TAIL
) -> list[dict[str, Any]]:
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
        llm: Any,
        tools_schema: list[dict[str, Any]],
        dispatch: Callable[[str, Any], dict[str, Any]],
        *,
        max_steps: int = 25,
        token_budget: int = 200_000,
        timeout_sec: int = 3600,
        risk_map: Optional[dict[str, str]] = None,
        approve_fn: Optional[Callable[[list[str]], bool]] = None,
        on_step: Optional[Callable[[int, dict[str, Any]], None]] = None,
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
        self.events: list[dict[str, Any]] = []   # tool_event 累积（随 LoopResult 返回）

    # ------------------------------------------------------------------ #
    def run(self, system: str, task: str) -> LoopResult:
        """执行循环直至终止；返回 LoopResult。"""
        started = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        failures = 0
        disabled = False
        compressed = False
        fail_lines: list[str] = []        # Working Memory 失败行（Phase 28 T2）
        ok_lines: list[str] = []          # Working Memory 成功行（Phase 29 T3）

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
                event = {"step": step, "name": name, "ok": ok}
                # 参数摘要（成功/失败行共用）
                if isinstance(arguments, str):
                    try:
                        args_obj = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        args_obj = arguments
                else:
                    args_obj = arguments
                args_brief = json.dumps(
                    args_obj if isinstance(args_obj, (dict, list)) else args_obj,
                    ensure_ascii=False, default=str)[:100]
                if not ok:
                    # 失败观察摘要进 events（Phase 28 T1：诊断器可见真实报错）
                    event["error"] = self._error_summary(obs)
                    # Working Memory 追加一行已试失败路径（Phase 28 T2）
                    fail_lines.append(
                        f"- step {step}: {name}({args_brief}) "
                        f"→ {event['error'][:200]}")
                else:
                    # 成功路径摘要（Phase 29 T3）：模型可见可复用结果
                    ok_lines.append(
                        f"- step {step}: {name}({args_brief}) "
                        f"→ OK: {self._ok_summary(obs)}")
                self.events.append(event)
                self._emit(step, {"event": "tool", "name": name, "ok": ok})
                if not ok:
                    step_all_ok = False

            if fail_lines or ok_lines:
                self._refresh_memory_message(messages, fail_lines, ok_lines)

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
    def _execute_tool(self, name: str, arguments: Any) -> dict[str, Any]:
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

    @staticmethod
    def _refresh_memory_message(messages: list[dict[str, Any]],
                                fail_lines: list[str],
                                ok_lines: list[str]) -> None:
        """把最近记忆写入/更新为紧随 system 的 user 消息（滚动窗口）。

        失败段（≤5 行，勿重复）与成功段（≤3 行，可复用结果）拼接；
        已存在同头消息则原地替换（保持消息条数不变）；被历史压缩丢弃后
        下次事件会自动重插。失败行格式与 Phase 28 完全一致。
        """
        del fail_lines[:-MEMORY_MAX_LINES]      # 失败滚动窗口
        del ok_lines[:-MEMORY_OK_MAX_LINES]     # 成功滚动窗口
        parts = [MEMORY_HEADER]
        if fail_lines:
            parts.append("### 失败（勿重复）")
            parts.extend(fail_lines)
        if ok_lines:
            parts.append("### 成功（可复用结果）")
            parts.extend(ok_lines)
        content = "\n".join(parts)
        for i, msg in enumerate(messages):
            if str(msg.get("content", "")).startswith(MEMORY_HEADER):
                msg["content"] = content
                return
        messages.insert(1, {"role": "user", "content": content})

    @staticmethod
    def _ok_summary(obs: dict[str, Any], limit: int = 80) -> str:
        """成功观察 → 产出摘要：stdout 优先，result 次之，空则 'ok'。"""
        for key in ("stdout", "result"):
            val = obs.get(key)
            if val:
                return str(val).strip()[:limit]
        return "ok"

    @staticmethod
    def _error_summary(obs: dict[str, Any], limit: int = 500) -> str:
        """观察 dict → 失败摘要："error_code: error_message" 形态，截断 500 字符。

        覆盖三种错误形态：skill 子进程（error_code+error_message）、
        异常/审批拒绝（error 单字段）、其他（首个非空候选）。
        """
        code = str(obs.get("error_code") or "").strip()
        detail = str(obs.get("error_message") or obs.get("error") or "").strip()
        if code and detail:
            return f"{code}: {detail}"[:limit]
        return (code or detail or "unknown error")[:limit]

    def _approx_tokens(self, messages: list[dict[str, Any]]) -> int:
        """粗估 token：消息 JSON 字符数 / 4。"""
        return sum(len(json.dumps(m, ensure_ascii=False, default=str))
                   for m in messages) // 4

    def _emit(self, step: int, event: dict[str, Any]) -> None:
        """触发 on_step 回调（CodingRunner 用于过程反馈节流）。"""
        if self.on_step is not None:
            try:
                self.on_step(step, event)
            except Exception:  # noqa: BLE001 —— 回调绝不影响主流程
                logger.exception("on_step callback failed")
