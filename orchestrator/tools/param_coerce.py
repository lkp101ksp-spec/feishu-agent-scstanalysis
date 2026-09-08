"""Phase 24：planner repr 串执行层纠正（spec §2）。

LLM/planner 偶发把 array/object 参数二次编码成 repr 字符串
（"['A','B']"）——plan JSON 整体合法，json.loads 层面查不出，字符串
原样流进 handler 才炸。本模块按 ToolSpec.parameters 声明类型在调
handler 前纠正：声明 array/object 但收到非空 str → json.loads →
ast.literal_eval 还原；解析失败或类型不匹配原样保留（不掩盖真错误）。
与既有点修（parse_gene_list / blocks serializer / _parse_literal）
幂等共存：它们收到的已是真 list/dict 时本纠正器不动。
"""
from __future__ import annotations

import ast
import json
import logging

logger = logging.getLogger(__name__)

# 声明类型 → 期望 Python 类型（仅 array/object；数字/布尔串 YAGNI 不纠正）
_COERCIBLE = {"array": list, "object": dict}


def _try_parse(raw: str):
    """str → 值：JSON 优先、Python repr 兜底；都失败返回 None。"""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, TypeError):
        return None


def coerce_params(parameters_schema: dict,
                  inputs: dict) -> tuple[dict, list[str]]:
    """按 schema 声明纠正 repr 串参数，返回 (纠正后 inputs 副本, 纠正参数名)。

    只动声明 array/object 且收到非空 str 的参数；解析结果类型须与声明
    匹配才生效。schema 畸形/任何意外异常一律原样保留，绝不向上抛。
    """
    try:
        props = (parameters_schema or {}).get("properties", {})
    except AttributeError:
        return inputs, []
    fixed = dict(inputs)
    coerced: list[str] = []
    for name, decl in props.items():
        try:
            decl_type = (decl or {}).get("type")
            expected = (_COERCIBLE.get(decl_type)
                        if isinstance(decl_type, str) else None)
            if expected is None:
                continue
            value = fixed.get(name)
            if not isinstance(value, str) or not value.strip():
                continue
            parsed = _try_parse(value)
            if isinstance(parsed, expected):
                fixed[name] = parsed
                coerced.append(name)
        except Exception:
            logger.warning("param coerce skipped for %s", name)
    return fixed, coerced
