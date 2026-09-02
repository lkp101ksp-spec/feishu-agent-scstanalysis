# Phase 24：planner repr 串执行层纠正 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ToolHandler.execute() 加 schema 驱动的 repr 串参数纠正，一次性覆盖全部现有+未来工具的 array/object 参数。

**Architecture:** 新纯函数模块 `orchestrator/tools/param_coerce.py`（json.loads → ast.literal_eval 双段还原，类型不匹配原样保留）；tool_handler.execute() 在调 handler 前调用并记 warning 日志；planner prompt 追加一句源头减量指引。

**Tech Stack:** 纯标准库（json/ast/logging）+ pytest（caplog/MagicMock）。

**Spec:** `docs/superpowers/specs/2026-09-02-planner-repr-coercion-design.md`

**环境：** Windows + PowerShell，工作目录 `i:\飞书agent`，测试命令统一为
`.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp`（单文件跑法把 `tests` 换成具体文件路径）。

---

### Task 1: param_coerce 纯函数模块（TDD）

**Files:**
- Create: `orchestrator/tools/param_coerce.py`
- Test: `tests/unit/test_param_coerce.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_param_coerce.py`：

```python
"""Phase 24：param_coerce.coerce_params 单测（spec §4）。"""
from orchestrator.tools.param_coerce import coerce_params

SCHEMA = {
    "type": "object",
    "properties": {
        "genes": {"type": "array"},
        "blocks": {"type": "object"},
        "name": {"type": "string"},
        "count": {"type": "integer"},
    },
}


def test_array_single_quote_repr():
    """单引号 repr 串（planner str() 强转典型产物）→ list。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "['A', 'B']"})
    assert fixed["genes"] == ["A", "B"]
    assert names == ["genes"]


def test_array_valid_json():
    """合法 JSON 串同样纠正。"""
    fixed, names = coerce_params(SCHEMA, {"genes": '["X"]'})
    assert fixed["genes"] == ["X"]
    assert names == ["genes"]


def test_object_repr():
    fixed, names = coerce_params(SCHEMA, {"blocks": "{'a': 1}"})
    assert fixed["blocks"] == {"a": 1}
    assert names == ["blocks"]


def test_unparseable_kept():
    """无法解析的 str 原样保留（交给下游校验报错，不掩盖真错误）。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "not a list"})
    assert fixed["genes"] == "not a list"
    assert names == []


def test_type_mismatch_kept():
    """声明 array 但解析出 dict → 原样保留。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "{'a': 1}"})
    assert fixed["genes"] == "{'a': 1}"
    assert names == []


def test_non_coercible_types_untouched():
    """string/integer 声明不动（YAGNI：真机未见数字串翻车）。"""
    fixed, names = coerce_params(SCHEMA, {"name": "'hi'", "count": "5"})
    assert fixed["name"] == "'hi'" and fixed["count"] == "5"
    assert names == []


def test_real_types_untouched():
    """已是真 list/dict 幂等不动（与既有点修共存）。"""
    fixed, names = coerce_params(SCHEMA, {"genes": ["A"], "blocks": {"a": 1}})
    assert fixed["genes"] == ["A"] and fixed["blocks"] == {"a": 1}
    assert names == []


def test_empty_string_untouched():
    fixed, names = coerce_params(SCHEMA, {"genes": "   "})
    assert fixed["genes"] == "   "
    assert names == []


def test_schema_without_properties():
    """schema 缺 properties 键 → 原样返回不炸。"""
    fixed, names = coerce_params({"type": "object"}, {"genes": "['A']"})
    assert fixed["genes"] == "['A']"
    assert names == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_param_coerce.py -q --basetemp=.pytest_tmp`
Expected: collection error（`orchestrator.tools.param_coerce` 不存在）

- [ ] **Step 3: 实现模块**

创建 `orchestrator/tools/param_coerce.py`：

```python
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
            expected = _COERCIBLE.get((decl or {}).get("type"))
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_param_coerce.py -q --basetemp=.pytest_tmp`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/param_coerce.py tests/unit/test_param_coerce.py
git commit -m "feat(phase24): param_coerce repr 串纠正纯函数（TDD）"
```

---

### Task 2: tool_handler.execute() 集成（TDD）

**Files:**
- Modify: `orchestrator/tools/tool_handler.py`
- Modify: `tests/unit/test_tool_handler_blocks.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_tool_handler_blocks.py` 末尾（该文件既有模式：
MagicMock registry + ToolSpec + `ToolHandler(registry=reg)`）：

```python
def test_tool_handler_coerces_repr_string_params(caplog):
    """Phase 24：repr 串参数在 execute() 被 schema 驱动纠正 + warning 日志。"""
    import logging

    seen = {}

    def handler(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d",
        parameters={"type": "object",
                    "properties": {"genes": {"type": "array"}}},
        risk_level="L0_read",
        handler=handler,
    )
    th = ToolHandler(registry=reg)
    with caplog.at_level(logging.WARNING):
        result = th.execute("x", {"genes": "['A', 'B']"},
                            actor_open_id="ou_1", session_id="s1")
    assert result.error_code is None
    assert seen["genes"] == ["A", "B"]
    assert "coerced from repr-string" in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_tool_handler_blocks.py -q --basetemp=.pytest_tmp`
Expected: 新用例 FAIL（handler 收到的是原 repr 串，`seen["genes"] == "['A', 'B']"` 而非 list）

- [ ] **Step 3: 集成到 execute()**

`orchestrator/tools/tool_handler.py` 两处改动（**串行**编辑，逐处确认——Phase 22 T3 竞态教训）。

改动 1——模块 import 区补：

```python
from orchestrator.tools.param_coerce import coerce_params
```

改动 2——`execute()` 中 AST notices 注入块（`outputs_extra` 填充）之后、
`try: out = spec.handler(**inputs)` 之前插入：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_tool_handler_blocks.py tests/unit/test_param_coerce.py -q --basetemp=.pytest_tmp`
Expected: 全 passed（3 既有 + 1 新 + 9）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/tool_handler.py tests/unit/test_tool_handler_blocks.py
git commit -m "feat(phase24): execute() 集成 repr 串纠正 + warning 日志（TDD）"
```

---

### Task 3: planner prompt 源头减量 + 全量回归 + 文档收尾

**Files:**
- Modify: `orchestrator/planner/planner.py`
- Modify: `docs/ROADMAP.md`
- Modify: `测试总结+2026-09-02T12-26-38.md`

- [ ] **Step 1: prompt 追加指引**

`orchestrator/planner/planner.py` 第 127 行：

```python
            "输出格式（严格遵循，只输出一个 JSON 对象，不要 markdown 围栏）：\n"
```

改为：

```python
            "输出格式（严格遵循，只输出一个 JSON 对象，不要 markdown 围栏；\n"
            "inputs 中数组/对象类型参数必须输出真 JSON 数组/对象，"
            "不要用字符串包裹的 Python repr）：\n"
```

- [ ] **Step 2: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp 2>&1 | Select-String "\d+ passed"`
Expected: ≥840 passed（830 + 本轮 10）；若有 planner prompt 快照断言类测试失败，检查并适配新文案

- [ ] **Step 3: 重启 ws_client 使新代码生效**

```powershell
.venv\Scripts\python.exe -m gateway.ws_client --force   # 后台终端
```
确认日志：`--force: terminating old ws_client` → Lark connected →
bio workspace gc sweeper 等其他 scanner 正常启动。
（纠正是被动兜底——LLM 输出正常时零行为变化；后续真机任务若 planner
再出 repr 串，日志可见 `param coerced from repr-string` warning，
作为长期复发率观测点记入测试总结。）

- [ ] **Step 4: ROADMAP 更新**

`docs/ROADMAP.md` 两处：
1. 持续项中 planner repr 串条目（若无单独条目则在变更记录体现；先 grep
   "repr" 确认现状）
2. Phase 23 节之后插入 Phase 24 节：
   ```markdown
   ## Phase 24：planner repr 串执行层纠正 — 已实施（2026-09-02）

   spec：`docs/superpowers/specs/2026-09-02-planner-repr-coercion-design.md`

   - [x] param_coerce 纯函数（array/object 声明 + str 值 → JSON/repr 双段还原，类型不匹配保留）
   - [x] ToolHandler.execute() 集成 + warning 日志（复发率观测点）
   - [x] planner prompt 源头减量指引
   ```
3. 变更记录追加：
   `| 2026-09-02 | Phase 24 planner repr 串长期方案：执行层 schema 驱动纠正（param_coerce + execute 集成 + warning 日志）+ prompt 源头减量 |`

- [ ] **Step 5: 更新测试总结 + 收尾 Commit**

`测试总结+2026-09-02T12-26-38.md` 追加 Phase 24 大节（任务/commit/回归数/
prompt 改动/后续观测建议）。

```bash
git add orchestrator/planner/planner.py docs/ROADMAP.md "测试总结+2026-09-02T12-26-38.md" docs/superpowers/plans/2026-09-02-phase24-planner-repr-coercion.md
git commit -m "feat(phase24): planner prompt 源头减量 + 文档收尾"
```

---

## Self-Review 结论

- **Spec 覆盖**：§1 三组件（T1 模块 / T2 execute 集成 / T3 prompt）、
  §2 算法（T1）、§3 双层错误兜底（T1 模块内 + T2 调用点 try/except）、
  §4 测试（T1 九用例 + T2 集成）、§5 验收（T3 回归 + 重启生效 + 观测点）。
- **占位符**：无——所有代码与命令完整。
- **类型一致性**：`coerce_params(parameters_schema, inputs) -> tuple[dict, list[str]]`
  在 T1 实现/测试、T2 调用点签名一致；prompt 改动位置（planner.py:127）
  已实地核实；test_tool_handler_blocks.py 的 ToolSpec/ToolHandler 构造
  模式与既有用例逐字对齐（ToolHandler(registry=reg) 无 settings 可跑通，
  execute 内 settings 有 getattr 守卫）。
