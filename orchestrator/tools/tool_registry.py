"""工具注册中心 + ToolSpec Pydantic 模型。

- ToolSpec：LLM 看的工具契约（OpenAI function calling schema）
- ToolRegistry：内存注册表 + OpenAI schema 转换 + L2 过滤
"""
from __future__ import annotations

from typing import Callable, Literal, Optional

from pydantic import BaseModel

from shared.errors import ToolNotFoundError

RiskLevel = Literal["L0_read", "L1_compute", "L2_side_effect"]


class ToolSpec(BaseModel):
    """LLM 看的工具契约。"""

    name: str
    description: str
    parameters: dict  # OpenAI JSON schema
    risk_level: RiskLevel
    handler: Callable
    requires_approval: bool = False
    timeout_sec: int = 60
    max_retries: int = 1
    tool_version: str = "1.0.0"
    approval_card_template: Optional[str] = None
    # Planner 可见性：False 时不出现在 DAG 规划 schema（stub 工具防误用，
    # Phase 12 板块④）；热加载/直调不受影响
    visible_to_planner: bool = True

    class Config:
        arbitrary_types_allowed = True

    def to_openai_function(self) -> dict:
        """转 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise ToolNotFoundError(f"tool {name!r} not registered")
        return self._tools[name]

    def list(
        self,
        risk_level: Optional[RiskLevel] = None,
        planner_visible: Optional[bool] = None,
    ) -> list[ToolSpec]:
        """列出工具；planner_visible 非 None 时按可见性过滤。"""
        items = list(self._tools.values())
        if risk_level is not None:
            items = [t for t in items if t.risk_level == risk_level]
        if planner_visible is not None:
            items = [t for t in items if t.visible_to_planner == planner_visible]
        return items

    def to_openai_functions(
        self, include_L2: bool = True, planner_visible: Optional[bool] = None
    ) -> list[dict]:
        items = self._tools.values()
        if not include_L2:
            items = [t for t in items if t.risk_level != "L2_side_effect"]
        if planner_visible is not None:
            items = [t for t in items if t.visible_to_planner == planner_visible]
        return [t.to_openai_function() for t in items]
