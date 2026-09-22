"""工具注册中心 + ToolSpec Pydantic 模型。

- ToolSpec：LLM 看的工具契约（OpenAI function calling schema）
- ToolRegistry：内存注册表 + OpenAI schema 转换 + L2 过滤
"""
from __future__ import annotations

from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict

from shared.errors import ToolNotFoundError

RiskLevel = Literal["L0_read", "L1_compute", "L2_side_effect"]


def parse_disabled_tools(raw: str) -> set[str]:
    """Phase 16 ACL：逗号分隔配置串 → 工具名集合（strip + 去空项）。

    " blast_search, run_python " → {"blast_search", "run_python"}；
    空串/全空白 → 空集（不禁用任何工具，与现状一致）。
    """
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


class ToolSpec(BaseModel):
    """LLM 看的工具契约。"""

    name: str
    description: str
    parameters: dict[str, Any]  # OpenAI JSON schema
    risk_level: RiskLevel
    handler: Callable[..., Any]
    requires_approval: bool = False
    timeout_sec: int = 60
    # 执行面统一挂账③（2026-09-17）：工具级容器资源覆写（docker
    # --cpus/--memory）；None=走 BioRunner 全局默认（settings.bio_cpus/
    # bio_memory）。与 timeout_sec 同模式双写：handler 显式传值 +
    # spec 声明 + 合同测试钉死一致（防"纸面覆写"）。
    cpus: Optional[str] = None
    memory: Optional[str] = None
    max_retries: int = 1
    tool_version: str = "1.0.0"
    approval_card_template: Optional[str] = None
    # Planner 可见性：False 时不出现在 DAG 规划 schema（stub 工具防误用，
    # Phase 12 板块④）；热加载/直调不受影响
    visible_to_planner: bool = True

    # Phase 22：class-based Config 弃用 → ConfigDict（原 Config 仅含
    # arbitrary_types_allowed=True，字段等价搬移）
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_openai_function(self) -> dict[str, Any]:
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

    def unregister(self, name: str) -> bool:
        """注销工具（skill 热刷新用）；不存在返回 False。"""
        return self._tools.pop(name, None) is not None

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
    ) -> List[dict[str, Any]]:  # 类体内 list 被同名方法遮蔽，注解须用 typing.List
        items = list(self._tools.values())
        if not include_L2:
            items = [t for t in items if t.risk_level != "L2_side_effect"]
        if planner_visible is not None:
            items = [t for t in items if t.visible_to_planner == planner_visible]
        return [t.to_openai_function() for t in items]
