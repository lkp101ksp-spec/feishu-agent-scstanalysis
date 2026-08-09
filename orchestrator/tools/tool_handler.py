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
        if spec.risk_level == "L1_compute":
            code = inputs.get("code") or ""
            try:
                self._ast.check(code)
            except ToolBlockedError as e:
                return ToolResult(
                    outputs={},
                    artifacts_ids=[],
                    error_code=e.code,
                    error_message=str(e),
                )
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
            return ToolResult(outputs=out, artifacts_ids=[])
        except Exception as e:
            return ToolResult(
                outputs={},
                artifacts_ids=[],
                error_code="TOOL_EXEC_FAILED",
                error_message=str(e),
            )