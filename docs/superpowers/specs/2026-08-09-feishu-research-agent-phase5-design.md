# 飞书科研闭环 Agent — Phase 5 设计稿

> 日期：2026-08-09
> 状态：Phase 5 设计稿（待实施）
> 范围：富文本块（6 类）+ 模板市场（Block + sub-Plan + 用户私有）
> 推迟：工具热加载 / 文件夹批量上传 / 本地 BLAST+ / AlphaFold（Phase 6）
> 前置：[Phase 1](./2026-08-08-feishu-research-agent-design.md) / [Phase 2](./2026-08-08-feishu-research-agent-phase2-design.md) / [Phase 3](./2026-08-09-feishu-research-agent-phase3-design.md) / [Phase 4 MVP](./2026-08-09-feishu-research-agent-phase4-mvp-design.md)
> 后置：Phase 5 实施计划 [`../plans/2026-08-09-feishu-research-agent-phase5.md`](../plans/2026-08-09-feishu-research-agent-phase5.md)（待写）

---

## 1. 范围与目标

### 1.1 Phase 5 主题

扩展**表达力**与**复用能力**：富文本块让 Plan 结果以结构化形式呈现，模板市场让用户跨工具 / 跨会话复用成功模式。

### 1.2 范围（2 大子系统）

| 子系统 | Phase 5 内容 | Phase 5 不做（推迟到 Phase 6）|
|---|---|---|
| **富文本块** | heading（h1-h3）/ code / quote / table / list / image | embed / divider / callout / equation |
| **模板市场** | Block模板 + sub-Plan 模板；用户私有；上传 / 列表 / 复用 / 参数化 | 群聊共享 / 公共模板 / 版本回滚 / UI卡片操作 |

### 1.3 关键约束（继承 Phase 1-4）

- 富文本块**不破坏 Plan DAG**：Planner 不直接生成富文本块
- 模板市场存 PostgreSQL，复用 Phase 1-4 session + 引擎
- sub-Plan 模板**仅引用现有 tool_name**（Phase 4 BLAST 等）；不内嵌 Python
- 模板权限：用户私有（owner_open_id 校验）
- 飞书 doc 渲染走 DocAdapter，限流 3 req/s（Phase 1）
- 模板参数化支持 `{{var}}` 占位符

### 1.4 关键决策（用户已批）

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 富文本产出位置 | ToolHandler + TemplateEngine 双路径 | 工具可主动出 blocks；否则模板兜底 |
| 2 | 模板市场边界 | Block + sub-Plan | MVP 不做 DAGNode 模板 |
| 3 | 模板作用域 | 用户私有 | MVP 不做群聊共享 |
| 4 | sub-Plan 与 DAG 关系 | 引用现有 tool_name | 不引入新执行逻辑 |
| 5 | 富文本覆盖 | 基础6 类 | heading/code/quote/table/list/image |
| 6 | 富文本生成位置 | 工具返回 `outputs.blocks` 或 TemplateEngine 兜底 | 与 Phase 4 兼容 |
| 7 | 模板参数化 | `{{var}}` 替换 | 最小可用 |
| 8 | 模板权限 | 私有（owner_open_id 校验）| MVP 不做 ACL |
| 9 | 模板存储 | PostgreSQL `templates` 表 | 与其他 ORM 共用 session |
| 10 | 模板市场 UI | 暂不做 IM 卡片（Phase 5 仅 API + CLI）| UI 推到 Phase 6 |

### 1.5 关键不做的清单（避免范围蔓延）

- 模板**版本回滚**（Phase 6：template_versions 表）
- 模板**导入导出**（Phase 6：JSON / YAML）
- 模板**搜索 / 标签**（Phase 6：ES / trigram）
- 富文本**math 公式 / Mermaid**（Phase 6）
- 富文本**视频 / 文件附件**（Phase 6）
- 富文本**协作评论**（Phase 6）
- **管理员审核流**（Phase 6）
- **多语言 / i18n**（Phase 6）

---

## 2. 总体架构

```
FastAPI (Phase 4 不变)
 └─ Orchestrator.process_phase5(incoming)
   ├─ /bind-doc-renew 指令（Phase 3 不变）
   ├─ /template-* 指令（Phase 5 新）
   └─ 普通消息
       ├─ Planner（Phase 3）→ DAGPlan（含可选 subplan_template_id）
       ├─ Scheduler（Phase 3）→ 展开 sub-Plan 模板
       ├─ LocalExecutor → ToolHandler.execute()
       │  └─ outputs.blocks (Phase 5 新字段，工具可声明)
       ├─ TemplateEngine.render_plan_summary(plan_result, outputs, template_blocks)
       └─ DocAdapter.render_blocks(doc_id, blocks) (Phase 5 升级)
```

**新增模块**：
- `orchestrator/blocks/`：6 类 block 的 Pydantic 模型 + 渲染器
- `orchestrator/templates/`：模板仓库 + 服务 + 渲染引擎
- `persistence/templates/`：ORM + Repo

**升级模块**：
- `ToolHandler`：支持 tool outputs 中 `blocks` 字段
- `TemplateEngine`：可消费模板市场中的模板
- `DocAdapter`：6 类 block → 飞书 doc API
- `Scheduler`：展开 sub-Plan 模板
- `Orchestrator`：process_phase5 入口 + `/template-*` 指令

---

## 3. 富文本块 schema 与类型

### 3.1 目标

统一 6 类 block 的 Pydantic 模型；提供 block 间互转 + 序列化为 JSON 字符串（用于模板存储）。

### 3.2 Block 类型（6 类）

| 类型 | 必填字段 | 可选字段 | 飞书 doc 映射 |
|---|---|---|---|
| **heading** | level（1-3）, text | - | heading1/2/3 block |
| **text** | text | - | text block（Phase 1 已有）|
| **code** | language, text | - | code block |
| **quote** | text | - | quote block |
| **table** | headers（list[str]）, rows（list[list[str]]）| - | table block |
| **list** | ordered（bool）, items（list[str]）| - | bullet / ordered block |
| **image** | url, alt | width, height | image block |

### 3.3 Pydantic schema

```python
# orchestrator/blocks/schemas.py
from typing import Literal, Optional
from pydantic import BaseModel, Field

class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=3)
    text: str

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class CodeBlock(BaseModel):
    type: Literal["code"] = "code"
    language: str = "plain"
    text: str

class QuoteBlock(BaseModel):
    type: Literal["quote"] = "quote"
    text: str

class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]

class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str]

class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None

# Union type
AnyBlock = Union[HeadingBlock, TextBlock, CodeBlock, QuoteBlock,
                  TableBlock, ListBlock, ImageBlock]
```

### 3.4 互转 JSON

```python
# orchestrator/blocks/serializer.py
import json
from typing import Any

def blocks_to_json(blocks: list[AnyBlock]) -> str:
    """list[Block] → JSON 字符串（用于模板存储）。"""
    return json.dumps([b.model_dump() for b in blocks], ensure_ascii=False)

def json_to_blocks(s: str) -> list[AnyBlock]:
    """JSON 字符串 → list[Block]（类型分发）。"""
    data = json.loads(s)
    return [_parse_block(b) for b in data]

def _parse_block(d: dict) -> AnyBlock:
    match d["type"]:
        case "heading": return HeadingBlock(**d)
        case "text":    return TextBlock(**d)
        case "code":    return CodeBlock(**d)
        case "quote":   return QuoteBlock(**d)
        case "table":   return TableBlock(**d)
        case "list":    return ListBlock(**d)
        case "image":   return ImageBlock(**d)
        case _: raise ValueError(f"unknown block type: {d['type']}")
```

### 3.5 Block 验证

- heading.level ∈ [1,3]
- table.rows 每行列数 == headers 数
- list.items 非空
- image.url 是 http/https

---

## 4. 模板市场

### 4.1 目标

让用户上传自己的 Block 模板 / sub-Plan 模板，跨会话复用。

### 4.2 模板类型

| 类型 | 内容 | 用例 |
|---|---|---|
| **BlockTemplate** | list[Block] 的 JSON 序列化 | "标准实验报告头 / 表格模板" |
| **SubPlanTemplate** | tool_name + inputs（含 `{{var}}` 占位符）| "搜索 BRCA1 → BLAST → 总结" |

### 4.3 Pydantic schema

```python
# orchestrator/templates/schemas.py
from pydantic import BaseModel, Field
from datetime import datetime

class SubPlanTemplateStep(BaseModel):
    """sub-Plan 模板中的一个 step（引用 Phase 4 tool_name）。"""
    step_id: str
    tool_name: str
    inputs: dict[str, str]  # 允许 "BRCA1" 或 "{{gene_name}}"

class TemplateBase(BaseModel):
    template_id: str
    owner_open_id: str
    name: str
    description: str = ""
    type: Literal["block", "subplan"]
    created_at: datetime
    updated_at: datetime

class BlockTemplate(TemplateBase):
    type: Literal["block"] = "block"
    blocks_json: str  # blocks_to_json 序列化的结果

class SubPlanTemplate(TemplateBase):
    type: Literal["subplan"] = "subplan"
    steps: list[SubPlanTemplateStep]
    join_strategy: Literal["sequential"] = "sequential"
```

### 4.4 模板 CRUD API（FastAPI 路由）

| Method | Path | 说明 |
|---|---|---|
| POST | `/templates/block` | 创建 Block 模板 |
| POST | `/templates/subplan` | 创建 sub-Plan 模板 |
| GET | `/templates/` | 列出当前用户的所有模板 |
| GET | `/templates/{template_id}` | 获取模板详情 |
| POST | `/templates/{template_id}/render` | 渲染模板（参数化） |
| DELETE | `/templates/{template_id}` | 删除（仅 owner）|

权限：所有路由校验 `owner_open_id == caller.open_id`；非 owner 返回 403。

### 4.5 模板参数化（`{{var}}` 替换）

```python
# orchestrator/templates/renderer.py
import re

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

def substitute(obj: str | dict | list, params: dict[str, str]) -> ...:
    """递归替换 {{var}} 占位符。"""
    if isinstance(obj, str):
        return _VAR_RE.sub(lambda m: params.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, dict):
        return {k: substitute(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, params) for v in obj]
    return obj
```

**渲染示例**：
```
模板 steps:
  step1: tool=blast_search, inputs={"query": "{{gene_query}}"}

render(params={"gene_query": "BRCA1 AND Homo sapiens"})
→ step1.inputs.query = "BRCA1 AND Homo sapiens"
```

### 4.6 模板服务（TemplateService）

```python
class TemplateService:
    def __init__(self, repo: TemplateRepo):
        self.repo = repo
    
    def create_block(self, *, owner_open_id: str, name: str,
                     blocks: list[AnyBlock], description: str = "") -> str:
        """创建 Block 模板。返回 template_id。"""
        template_id = new_ulid()
        self.repo.upsert(
            template_id=template_id, owner_open_id=owner_open_id,
            name=name, type="block",
            blocks_json=blocks_to_json(blocks),
            description=description,
        )
        return template_id
    
    def create_subplan(self, *, owner_open_id: str, name: str,
                        steps: list[SubPlanTemplateStep],
                        description: str = "") -> str:
        """创建 sub-Plan 模板。"""
        template_id = new_ulid()
        self.repo.upsert(
            template_id=template_id, owner_open_id=owner_open_id,
            name=name, type="subplan",
            steps_json=json.dumps([s.model_dump() for s in steps]),
            description=description,
        )
        return template_id
    
    def list_by_owner(self, owner_open_id: str) -> list[TemplateRow]:
        return self.repo.list_by_owner(owner_open_id)
    
    def get(self, template_id: str) -> TemplateRow:
        return self.repo.get(template_id)
    
    def render_block(self, template_id: str, params: dict) -> list[AnyBlock]:
        tpl = self.get(template_id)
        if tpl.type != "block":
            raise ValueError(f"template {template_id} is not a block template")
        blocks = json_to_blocks(tpl.blocks_json)
        return [b.model_copy(update=...) for b in blocks]  # 替换 var
    
    def render_subplan(self, template_id: str, params: dict) -> list[SubPlanTemplateStep]:
        tpl = self.get(template_id)
        if tpl.type != "subplan":
            raise ValueError(...)
        return [SubPlanTemplateStep(
            step_id=s.step_id, tool_name=s.tool_name,
            inputs=substitute(s.inputs, params),
        ) for s in tpl.steps]
    
    def delete(self, *, template_id: str, caller_open_id: str) -> None:
        tpl = self.get(template_id)
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(f"not owner of template {template_id}")
        self.repo.delete(template_id)
```

---

## 5. 模板参数化（`{{var}}` 替换）

### 5.1 目标

让模板支持变量占位符，渲染时由 caller 提供。

### 5.2 占位符规则

- 格式：`{{var_name}}`，var_name 是字母 / 数字 / 下划线
- 替换：仅在 string 类型的值中替换
- 未提供：保留原 `{{var}}` 字面量

### 5.3 实现

见 §4.5 `substitute()`。

### 5.4 错误处理

- 模板含 `{{var}}` 但 caller 未提供：保留原字面量
- 模板含非法占位符（如 `{{}}`）：renderer 忽略
- 模板超长（>1MB）：拒绝创建（422）

---

## 6. ToolHandler 双路径

### 6.1 目标

工具可在 outputs 中声明 `blocks`，由 ToolHandler 提取并附加到 plan result。

### 6.2 实现

```python
# orchestrator/tools/tool_handler.py （升级）
def execute(self, tool_name, inputs, *, actor_open_id="", session_id=""):
    spec = self.registry.get(tool_name)
    ...  # AST + approval（Phase 2/3 不变）
    try:
        out = spec.handler(**inputs)
        if not isinstance(out, dict):
            out = {"result": out}
        out.update(outputs_extra)
        # Phase 5: 提取 blocks（如有）
        blocks_raw = out.pop("blocks", None)
        tool_err = out.pop("error_code", None)
        tool_err_msg = out.pop("error_message", None)
        return ToolResult(
            outputs=out,
            artifacts_ids=[],
            error_code=tool_err,
            error_message=tool_err_msg,
            blocks=[_parse_block(b) for b in blocks_raw] if blocks_raw else None,
        )
    except Exception as e:
        ...
```

### 6.3 ToolResult 升级

```python
@dataclass
class ToolResult:
    outputs: dict
    artifacts_ids: list[str]
    error_code: str | None = None
    error_message: str | None = None
    blocks: list[AnyBlock] | None = None  # Phase 5 新增
```

### 6.4 工具使用示例

```python
# 某个 L1_compute 工具（如 bio_summary）可直接产出富文本
def summarize_tool(*, blast_results):
    table = TableBlock(
        headers=["Hit ID", "Title", "Length"],
        rows=[
            [r["id"], r["title"], str(r["length"])]
            for r in blast_results["records"]
        ],
    )
    return {
        "summary": "Top 5 hits found",
        "blocks": [table.model_dump()],
    }
```

### 6.5 TemplateEngine 兜底

当工具未产出 blocks 时，`TemplateEngine.render_plan_summary` 仍按 Phase 2 渲染 text blocks：

```python
def render_plan_summary(self, status, node_states, artifacts_count):
    """Phase 5: 返回 list[Block] 而非纯 text。"""
    return [
        HeadingBlock(level=2, text=f"Plan {status}"),
        TextBlock(text=f"Nodes: {len(node_states)}; Artifacts: {artifacts_count}"),
        TableBlock(
            headers=["Node", "State"],
            rows=[[nid, state] for nid, state in node_states.items()],
        ),
    ]
```

---

## 7. DocAdapter 飞书渲染（6 类 block）

### 7.1 目标

将 list[Block] 渲染为飞书 doc API 调用。

### 7.2 飞书 doc block 类型映射

| 我们的 Block | 飞书 API block type |
|---|---|
| heading level=1 | heading1 |
| heading level=2 | heading2 |
| heading level=3 | heading3 |
| text | text |
| code | code |
| quote | quote_container（或 text + 引用样式）|
| table | table |
| list ordered | ordered_list |
| list unordered | bullet_list |
| image | image |

### 7.3 DocAdapter 升级

```python
# feishu_adapter/doc_adapter.py （升级）
class DocAdapter:
    def render_blocks(self, doc_id: str, blocks: list[AnyBlock]):
        """把 blocks 批量追加到 doc。"""
        for block in blocks:
            self._rate_limiter.wait()  # Phase 5 限流（继承 Phase 1）
            payload = self._to_feishu_payload(block)
            self._post_block(doc_id, payload)
    
    def _to_feishu_payload(self, block: AnyBlock) -> dict:
        match block.type:
            case "heading":
                return {"block_type": f"heading{block.level}",
                        f"heading{block.level}": {"elements": [
                            {"text_run": {"content": block.text}}
                        ]}}
            case "text":
                return {"block_type": "text",
                        "text": {"elements": [{"text_run": {"content": block.text}}]}}
            case "code":
                return {"block_type": "code",
                        "code": {"elements": [{"text_run": {"content": block.text}}],
                                 "language": block.language}}
            case "quote":
                return {"block_type": "quote_container",
                        "quote_container": [{"block_type": "text",
                                             "text": {"elements": [{"text_run": {"content": block.text}}]}}]}
            case "table":
                return {"block_type": "table",
                        "table": {"property": {
                            "row_size": len(block.rows) + 1,
                            "column_size": len(block.headers),
                        }, "cells": [...]}}
            case "list":
                list_type = "ordered_list" if block.ordered else "bullet_list"
                return {"block_type": list_type, list_type: {...}}
            case "image":
                return {"block_type": "image",
                        "image": {"url": block.url, "alt": block.alt}}
```

### 7.4 限流

复用 Phase 1 RateLimiter（3 req/s）。Phase 5 加 `DocRateLimiter` 包装（限制 doc API 调用）。

---

## 8. Scheduler 展开 sub-Plan

### 8.1 目标

DAGNode 可声明 `subplan_template_id` + `params`，Scheduler 在执行前展开为内联 DAGNode 列表。

### 8.2 DAGNode 升级

```python
class DAGNode(BaseModel):
    ...  # Phase 3 字段
    # === Phase 5 ===
    subplan_template_id: Optional[str] = None
    subplan_params: dict[str, str] = Field(default_factory=dict)
```

### 8.3 展开逻辑

```python
# orchestrator/planner/scheduler.py （Phase 5 升级）
def _expand_subplan(self, node: DAGNode) -> list[DAGNode]:
    if not node.subplan_template_id:
        return [node]
    tpl = self.template_service.get(node.subplan_template_id)
    if tpl.type != "subplan":
        raise ValueError(f"template {node.subplan_template_id} is not subplan")
    steps = self.template_service.render_subplan(
        node.subplan_template_id, node.subplan_params
    )
    # 转 DAGNode 列表（继承 node.depends_on）
    return [
        DAGNode(
            node_id=f"{node.node_id}_step_{i}",
            kind="tool",
            tool_name=step.tool_name,
            inputs=step.inputs,
            depends_on=[node.depends_on[0]] if i == 0 and node.depends_on else [],
        )
        for i, step in enumerate(steps)
    ]
```

### 8.4 校验

`validate_dag` 增加：subplan_template_id 必须存在（由 template_service.get 校验）。

### 8.5 与 Planner 的关系

Planner（Phase 3）不直接引用模板；用户可在 IM 中发"使用模板 tpl_xxx with gene=BRCA1" 触发：
- Plan text 含 `{{tpl_xxx(gene=BRCA1)}}` 标记
- Phase 5 在 `process_phase5` 中预解析标记，注入 DAGNode.subplan_template_id

**Phase 5 简化**：由 IM 指令 `/use-template <id> <params>` 显式触发（而非 LLM 解析）。

---

## 9. PostgreSQL schema 增量

### 9.1 新增表

```sql
CREATE TABLE templates (
    template_id      TEXT PRIMARY KEY,
    owner_open_id    TEXT NOT NULL,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    type             TEXT NOT NULL,         -- 'block' | 'subplan'
    blocks_json      TEXT,                  -- type='block' 时的 JSON
    steps_json       TEXT,                  -- type='subplan' 时的 JSON
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    archived_at      TIMESTAMP WITH TIME ZONE  -- Phase 6 软删除
);
CREATE INDEX ix_templates_owner ON templates (owner_open_id);
CREATE INDEX ix_templates_owner_name ON templates (owner_open_id, name);
```

### 9.2 Phase 5 不做

- `template_versions` 表（Phase 6）
- `template_tags` / `template_search`（Phase 6）

### 9.3 迁移

创建 `migrations/versions/0005_phase5_templates.py`，与 Phase 1-4 共用 alembic 链路。

---

## 10. 测试策略

### 10.1 测试矩阵

| 子系统 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| Block schemas / serializer | 6 | - | - |
| TemplateRenderer / substitute | 4 | - | - |
| TemplateRepo CRUD | 3 | - | - |
| TemplateService 权限 | 3 | - | - |
| ToolHandler blocks 透传 | 2 | - | - |
| TemplateEngine 富文本 | 2 | - | - |
| DocAdapter 渲染 | - | 6 | - |
| FastAPI /templates/* | - | 5 | - |
| Scheduler 展开 sub-Plan | - | 2 | - |
| E1-E8 端到端 | - | - | 8 |
| **合计** | **20** | **13** | **8** |

### 10.2 E1-E8 端到端场景

| # | 场景 | 验证点 |
|---|---|---|
| E1 | 上传 Block 模板 → 复用 → 飞书 doc 渲染 6 类 block | heading + code + quote + table + list + image |
| E2 | sub-Plan 模板上传 → Planner DAG → Scheduler 展开 → 执行 | blast_search → summary → 文档 |
| E3 | 模板参数化 `{{gene}}` → 渲染时替换 | 输入 BRCA1 → BLAST query 替换为 BRCA1 |
| E4 | 模板权限隔离 | 用户 A 上传，用户 B GET → 403 |
| E5 | ToolHandler 双路径 | tool 直接出 blocks vs TemplateEngine 兜底 |
| E6 | DocAdapter 飞书 doc 限流 | 连续10次渲染，最后 ≤ 1s 等待 |
| E7 | Block schema 验证 | table.rows 列数不匹配 → ValidationError |
| E8 | FastAPI /templates/* 全链路 | create → list → render → delete |

### 10.3 mock 策略

- **DocAdapter mock**：fake feishu API，捕获 block payloads
- **TemplateService 集成**：用 in-memory SQLite + Phase 4 fixtures

### 10.4 覆盖率目标

| 模块 | 目标 |
|---|---|
| `orchestrator/blocks/` | ≥ 90% |
| `orchestrator/templates/` | ≥ 85% |
| `orchestrator/templates/renderer.py` | ≥ 90% |
| DocAdapter 渲染逻辑 | ≥ 80% |
| **Phase 5 新增整体** | ≥ 80% |

### 10.5 回归保证

- Phase 1-4 全部 230 测试**必须**继续通过
- DocAdapter 升级不影响 Phase 1 `append_blocks(text_blocks)` 路径
- ToolHandler blocks 字段为 None 时不影响现有路径

---

## 11. Phase 5 不做（明确边界）

| 项 | 推迟到 | 原因 |
|---|---|---|
| 富文本 embed / divider / callout / equation | Phase 6 | MVP 6 类足够 |
| 模板版本回滚 / template_versions 表 | Phase 6 | MVP 不做 |
| 模板导入导出 | Phase 6 | MVP 仅 API + DB |
| 模板搜索 / 标签 / ES | Phase 6 | MVP 仅按 owner 列表 |
| 群聊级 / 全员公共模板 | Phase 6 | MVP 用户私有 |
| 富文本 math / Mermaid / 视频 / 文件附件 | Phase 6 | MVP 仅 6 类 |
| 富文本协作评论 | Phase 6 | MVP 单向写入 |
| 管理员审核流 | Phase 6 | MVP 用户自管 |
| 模板市场 IM 卡片 UI | Phase 6 | MVP 仅 API + CLI |
| 多语言 / i18n | Phase 6 | MVP 中文 |
| 工具热加载 | Phase 6 | 已推迟 |
| 文件夹批量上传 | Phase 6 | 已推迟 |
| 本地 BLAST+ / AlphaFold | Phase 6 | 已推迟 |
| 多用户合签 / 群聊合签 | Phase 6 | 已推迟 |
| 选择性遗忘 / symlink | Phase 6 | 已推迟 |

---

## 12. 风险与决策

### 12.1 已识别风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 飞书 doc API block 类型映射不全 | Phase 5 6 类有限，足够；剩余 Phase 6 |
| R2 | 模板超长（blocks_json  >1MB）| create 时校验长度（422）|
| R3 | sub-Plan 模板循环引用（a 引用 b, b 引用 a）| 展开时检查已展开 template_ids |
| R4 | 飞书 doc 渲染限流 3 req/s | DocRateLimiter 包装（复用 Phase 1）|
| R5 | ToolHandler blocks 字段污染既有 outputs | Phase 5 显式从 out.pop("blocks") 提取，不影响其他字段 |
| R6 | TemplateService 权限漏洞 | 所有路由强制 owner_open_id 校验 + 单元测试覆盖 |
| R7 | Phase 4 MVP BLAST 不产出 blocks | BLAST 仍输出 dict；TemplateEngine 兜底渲染 |
| R8 | 模板 store 与其他 schema 命名冲突 | 字段加 phase5_ 前缀（如 phase5_blocks）|

### 12.2 ADR（决策记录）待写

| # | ADR 主题 |
|---|---|
| 0006 | 富文本 block 类型选型（6 类 vs 完整 12 类）|
| 0007 | 模板市场用户私有 vs 群聊共享 |
| 0008 | sub-Plan 模板与现有 DAG 的关系（引用 tool_name vs 内嵌 Python）|
| 0009 | DocAdapter 6 类 block → 飞书 API 映射策略 |

---

## 13. ADR 清单（Phase 5）

待 Phase 5 实施前写 4 个 ADR：

1. **0006 phase5-block-types**：6 类 MVP（heading/code/quote/table/list/image）+ Phase 6 扩展
2. **0007 phase5-template-privacy**：用户私有（owner_open_id 校验）
3. **0008 phase5-subplan-relation**：sub-Plan 模板引用现有 tool_name（不内嵌 Python）
4. **0009 phase5-doc-block-mapping**：DocAdapter 6 类 block → 飞书 doc API 映射

---

## 14. 后续动作

**Phase 5 实施前**：

1. ✅ **写完本 spec**：14 章全部展开
2. ⏭️ **写 Phase 5 plan**（writing-plans skill）
3. ⏭️ **写 4 个 ADR**（决策记录）
4. ⏭️ **用户评审 plan + ADR**
5. ⏭️ **Inline 实施**（沿用 Phase 3-4 节奏）

**Phase 6 候选范围**（基于 Phase 5 完成度）：

| 模块 | Phase 6 候选 |
|---|---|
| 富文本扩展 | embed + divider + callout + equation + math + Mermaid + 视频 + 文件附件 |
| 模板版本 | template_versions 表 + 回滚 |
| 模板导入导出 | JSON / YAML |
| 模板搜索 | tags + 全文搜索（ES / trigram）|
| 模板协作 | 群聊级 / 全员公共 / 管理员审核 |
| 工具热加载 | 管理员上传 .py → AST check |
| 文件夹批量上传 | 递归扫描 + 进度 + 权限 |
| 领域工具 | 本地 BLAST+ / AlphaFold / UniProt / 多序列比对 |
| Phase 2/3/4 推迟项 | while/for 嵌套 / break/continue / 自动重试 / 总结失真 / 选择性遗忘 / symlink / 群聊合签 |

**Phase 5 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 14 章用户确认 |
| ADR 门 | 4 个 ADR 用户评审 |
| 实施门 | plan 用户评审通过 |
| 测试门 | 41 新测试全部通过；230 老测试 0 回归 |
| 演示门 | "上传模板 → 复用 → 富文本飞书 doc" 全链路跑通 |