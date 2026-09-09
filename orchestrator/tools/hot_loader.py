"""Phase 6: 工具热加载（管理员上传 + AST P0 拦截 + ToolRegistry 注入）。"""
from __future__ import annotations

import importlib.util
import os
from typing import TYPE_CHECKING, Any, cast

from shared.ulid_ import new_ulid

if TYPE_CHECKING:
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.tool_registry import ToolRegistry


class HotLoader:
    MAX_CODE_SIZE = 100 * 1024

    def __init__(
        self, *,
        tool_registry: "ToolRegistry",
        audit_repo: Any,
        ast_guard: "ASTGuard",
        temp_dir: str = "/tmp/hot_tools",
    ) -> None:
        self.tool_registry = tool_registry
        self.audit_repo = audit_repo
        self.ast_guard = ast_guard
        self.temp_dir = temp_dir

    def upload(
        self, *,
        name: str,
        code: str,
        parameters: dict[str, Any],
        risk_level: str,
        actor_open_id: str,
    ) -> str:
        if len(code.encode()) > self.MAX_CODE_SIZE:
            raise ValueError(
                f"code too large: {len(code)} > {self.MAX_CODE_SIZE}"
            )
        from shared.errors import ToolBlockedError
        try:
            result = self.ast_guard.check(code)
        except ToolBlockedError as e:
            if self.audit_repo is not None:
                self.audit_repo.write(
                    audit_id=new_ulid(),
                    action="blocked_tool_upload",
                    actor_type="admin", actor_id=actor_open_id,
                    target_type="tool", target_id=name,
                    detail={"reason": str(e)},
                )
            raise
        if result.blocked:
            if self.audit_repo is not None:
                self.audit_repo.write(
                    audit_id=new_ulid(),
                    action="blocked_tool_upload",
                    actor_type="admin", actor_id=actor_open_id,
                    target_type="tool", target_id=name,
                    detail={"notices": str(result.notices)},
                )
            raise ToolBlockedError(result.notices)
        os.makedirs(self.temp_dir, exist_ok=True)
        module_id = new_ulid()
        path = os.path.join(self.temp_dir, f"{module_id}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        spec = importlib.util.spec_from_file_location(module_id, path)
        if spec is None or spec.loader is None:
            raise ValueError(f'cannot create module spec for {path}')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handle = getattr(module, "handle", None)
        if handle is None:
            raise ValueError("tool must define handle(**kwargs)")
        from orchestrator.tools.tool_registry import RiskLevel, ToolSpec
        self.tool_registry.register(ToolSpec(
            name=name, description="hot-loaded",
            parameters=parameters, risk_level=cast(RiskLevel, risk_level),
            handler=handle,
        ))
        if self.audit_repo is not None:
            self.audit_repo.write(
                action="hot_load_tool",
                actor_type="admin", actor_id=actor_open_id,
                target_type="tool", target_id=name,
                detail={"module_id": module_id},
            )
        return name
