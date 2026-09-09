"""工具调用执行：AST → 审批 → handler() → 返回结构化结果。

异常类型：ToolBlockedError / ToolDeniedError → 转 ExecutionState.FAILED。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from orchestrator.tools.ast_guard import ASTGuard
from orchestrator.tools.param_coerce import coerce_params
from orchestrator.tools.tool_registry import ToolRegistry, parse_disabled_tools
from shared.errors import ToolBlockedError

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    outputs: dict[str, Any]
    artifacts_ids: list[str]
    error_code: str | None = None
    error_message: str | None = None
    blocks: list[Any] | None = None  # Phase 5: 富文本块


class ToolHandler:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        settings: Any = None,
    ) -> None:
        self.registry = registry
        self._ast = ASTGuard()
        # Phase 16 ACL：settings.disabled_tools 禁用名单（执行层兜底校验）
        self.settings = settings

    def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        *,
        actor_open_id: str = "",
        session_id: str = "",
    ) -> ToolResult:
        spec = self.registry.get(tool_name)
        # Phase 16 ACL：执行层禁用名单兜底（planner 已过滤，防绕过规划直呼）
        disabled = parse_disabled_tools(
            getattr(self.settings, "disabled_tools", "") or ""
        ) if self.settings is not None else set()
        if tool_name in disabled:
            logger.warning("tool %s blocked by ACL (disabled list)", tool_name)
            return ToolResult(
                outputs={},
                artifacts_ids=[],
                error_code="TOOL_DISABLED",
                error_message=(
                    f"tool {tool_name!r} is disabled by system administrator"
                ),
            )
        # L1: AST check
        ast_report = None
        if spec.risk_level == "L1_compute":
            code = inputs.get("code") or ""
            try:
                ast_report = self._ast.check(code)
            except ToolBlockedError as e:
                return ToolResult(
                    outputs={},
                    artifacts_ids=[],
                    error_code=e.code,
                    error_message=str(e),
                )
        # L1: P1/P2 notices 通过 outputs.ast_notices 透传（Phase 3）
        outputs_extra: dict[str, Any] = {}
        if ast_report is not None and ast_report.notices:
            outputs_extra["ast_notices"] = [
                {"level": n[0], "message": n[1], "line": n[2]}
                for n in ast_report.notices
            ]
        # Phase 24：planner repr 串执行层纠正（array/object 参数收到 str
        # 时按 schema 还原；coerce 自身异常不阻断执行，用原 inputs 继续）
        try:
            inputs, coerced = coerce_params(spec.parameters, inputs)
            if coerced:
                logger.warning(
                    "tool %s param coerced from repr-string: %s",
                    tool_name, coerced)
        except Exception:
            logger.exception("param coerce failed for %s", tool_name)
        # L2 审批：Phase 17 起由 scheduler 层 l2_gate 负责（research 链路
        # write_doc 节点卡片确认）；原 Phase 2 approval stub（request_sync
        # mock，bind 不覆盖恒 deny）已删除——L2 均不可直呼时该分支不可达
        try:
            out = spec.handler(**inputs)
            # handler 直接返回 ToolResult（如 skill 子进程工具）时透传，
            # 保留 error_code/error_message，仅注入 AST notices；
            # 此前走 dict 包装会把 ToolResult 包成 result repr 丢 error_code
            if isinstance(out, ToolResult):
                if outputs_extra:
                    out.outputs = {**out.outputs, **outputs_extra}
                return out
            if not isinstance(out, dict):
                out = {"result": out}
            out.update(outputs_extra)  # 注入 AST P1/P2 notices
            # Phase 4：handler 内部错误（error_code in dict）→ 透传到 ToolResult
            tool_err = out.pop("error_code", None)
            tool_err_msg = out.pop("error_message", None)
            # Phase 5：handler 返回 blocks → 提取为 AnyBlock 列表
            # 解析失败仅降级告警，不炸工具本身（真机 2026-08-30：
            # read_doc 曾因 blocks 键撞名被误解析导致整节点失败）
            blocks_raw = out.pop("blocks", None)
            blocks = None
            if blocks_raw:
                import json

                from orchestrator.blocks.serializer import json_to_blocks
                try:
                    blocks = json_to_blocks(json.dumps(blocks_raw))
                except Exception:
                    logger.warning(
                        "tool %s blocks 输出解析失败，忽略富文本块", tool_name
                    )
                    blocks = None
            return ToolResult(
                outputs=out,
                artifacts_ids=[],
                error_code=tool_err,
                error_message=tool_err_msg,
                blocks=blocks,
            )
        except Exception as e:
            return ToolResult(
                outputs=outputs_extra,
                artifacts_ids=[],
                error_code="TOOL_EXEC_FAILED",
                error_message=str(e),
            )
