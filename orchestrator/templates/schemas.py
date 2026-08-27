"""Phase 5 模板相关 Pydantic 模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubPlanTemplateStep(BaseModel):
    """sub-Plan 模板中的一个 step（引用 Phase 4 tool_name）。"""
    step_id: str
    tool_name: str
    inputs: dict[str, str] = Field(default_factory=dict)


class BlockTemplateData(BaseModel):
    """模板响应中的 Block 模板详情（不含 blocks_json，由 caller 解析）。"""
    template_id: str
    owner_open_id: str
    name: str
    description: str
    type: Literal["block"]
    blocks_json: str


class SubPlanTemplateData(BaseModel):
    template_id: str
    owner_open_id: str
    name: str
    description: str
    type: Literal["subplan"]
    steps: list[SubPlanTemplateStep]
