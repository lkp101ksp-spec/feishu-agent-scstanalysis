"""Phase 5 模板渲染：{{var}} 替换。"""
from __future__ import annotations

import re
from typing import Any

from orchestrator.templates.schemas import SubPlanTemplateStep

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def substitute(obj: Any, params: dict[str, str]) -> Any:
    """递归替换 {{var}} 占位符。"""
    if isinstance(obj, str):
        return _VAR_RE.sub(lambda m: params.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, dict):
        return {k: substitute(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, params) for v in obj]
    return obj


def render_block(blocks_json: str, params: dict[str, str]) -> list[Any]:
    """渲染 Block 模板：先用 substitute 替换 blocks_json 中的 {{var}}，再解析。"""
    import json

    from orchestrator.blocks.serializer import json_to_blocks
    raw = json.loads(blocks_json)
    raw_subst = substitute(raw, params)
    return json_to_blocks(json.dumps(raw_subst, ensure_ascii=False))


def render_subplan(
    steps: list[SubPlanTemplateStep], *, params: dict[str, str]
) -> list[SubPlanTemplateStep]:
    """渲染 sub-Plan 模板：替换每个 step 的 inputs 中的 {{var}}。"""
    return [
        SubPlanTemplateStep(
            step_id=s.step_id, tool_name=s.tool_name,
            inputs=substitute(s.inputs, params),
        )
        for s in steps
    ]
