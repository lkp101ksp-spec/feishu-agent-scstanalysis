"""工具调用执行：AST → 审批 → handler() → 返回结构化结果。

异常类型：ToolBlockedError / ToolDeniedError → 转 ExecutionState.FAILED。
"""
from __future__ import annotations

from dataclasses import dataclass

from orchestrator.tools.ast_guard import ASTGuard
from orchestrator.tools.tool_registry import ToolRegistry
from shared.errors import ToolBlockedError


@dataclass
class ToolResult:
    outputs: dict
    artifacts_ids: list[str]
    error_code: str | None = None
    error_message: str | None = None
    blocks: list | None = None  # Phase 5: 富文本块


class ToolHandler:
    def __init__(self, registry: ToolRegistry, *, approval_service=None) -> None:
        self.registry = registry
        self._ast = ASTGuard()
        self._approval = approval_service

    def execute(
        self,
        tool_name: str,
        inputs: dict,
        *,
        actor_open_id: str = "",
        session_id: str = "",
    ) -> ToolResult:
        spec = self.registry.get(tool_name)
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
        outputs_extra: dict = {}
        if ast_report is not None and ast_report.notices:
            outputs_extra["ast_notices"] = [
                {"level": n[0], "message": n[1], "line": n[2]}
                for n in ast_report.notices
            ]
        # L2: approval stub
        if spec.risk_level == "L2_side_effect" and self._approval is not None:
            ok = self._approval.request_sync(
                tool_name=tool_name,
                args_preview=inputs,
                actor_open_id=actor_open_id,
                session=type(
                    "S",
                    (),
                    {"bound_doc_id": None, "bind_expires_at": None},
                )(),
            )
            if not ok:
                return ToolResult(
                    outputs={},
                    artifacts_ids=[],
                    error_code="TOOL_DENIED",
                    error_message="denied",
                )
        try:
            out = spec.handler(**inputs)
            if not isinstance(out, dict):
                out = {"result": out}
            out.update(outputs_extra)  # 注入 AST P1/P2 notices
            # Phase 4：handler 内部错误（error_code in dict）→ 透传到 ToolResult
            tool_err = out.pop("error_code", None)
            tool_err_msg = out.pop("error_message", None)
            # Phase 5：handler 返回 blocks → 提取为 AnyBlock 列表
            blocks_raw = out.pop("blocks", None)
            blocks = None
            if blocks_raw:
                import json

                from orchestrator.blocks.serializer import json_to_blocks
                blocks = json_to_blocks(json.dumps(blocks_raw))
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
