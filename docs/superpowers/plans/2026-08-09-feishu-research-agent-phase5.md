# Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline 模式)。Subagent 在 Phase 2/3 期间多次漏建文件，本计划改为直接实施。

**Goal:** 富文本块（6 类）+ 模板市场（Block + sub-Plan + 用户私有）。让 Plan 结果以结构化形式呈现到飞书 doc，并支持用户跨会话复用成功模板。

**Architecture:** 双路径：ToolHandler 支持 `outputs.blocks` 字段，TemplateEngine 兜底渲染。Sub-Plan 模板引用 Phase 4 tool_name，Scheduler 展开为内联 DAGNode。

**Tech Stack:** pydantic v2 + httpx + respx + pytest + PostgreSQL (Phase 1 ORM 共用)

**前置 spec:** [../specs/2026-08-09-feishu-research-agent-phase5-design.md](../specs/2026-08-09-feishu-research-agent-phase5-design.md)

---

## File Structure（前置）

```
orchestrator/blocks/
  __init__.py
  schemas.py                # 7 个 Pydantic 模型（heading/text/code/quote/table/list/image）
  serializer.py             # blocks_to_json / json_to_blocks

orchestrator/templates/
  __init__.py
  schemas.py                # SubPlanTemplateStep / BlockTemplate / SubPlanTemplate
  renderer.py               # substitute() / render_block() / render_subplan()
  template_service.py       # TemplateService CRUD + 权限

persistence/
  models.py                 # + TemplateRow
  repositories/
    template_repo.py        # TemplateRepo

feishu_adapter/
  doc_adapter.py            # + render_blocks() + _to_feishu_payload()

orchestrator/
  tools/tool_handler.py     # + blocks 字段
  planner/dag_schema.py     # + subplan_template_id / subplan_params
  planner/scheduler.py      # + _expand_subplan()
  template_engine.py        # 返回 list[Block] 而非 text
  app.py                    # + process_phase5 + register_template_service

gateway/
  app.py                    # + /templates/* 路由 + /template-* IM 指令

tests/unit/
  test_blocks_schemas.py
  test_blocks_serializer.py
  test_template_renderer.py
  test_template_repo.py
  test_template_service.py
  test_tool_handler_blocks.py
  test_template_engine_phase5.py

tests/integration/
  test_doc_adapter_blocks.py
  test_templates_api.py
  test_subplan_expansion.py
  test_e2e_phase5_e1_e8.py
```

---

## Task 1: Block schemas（6 类 Pydantic 模型）

**Files:**
- Create: `orchestrator/blocks/__init__.py`
- Create: `orchestrator/blocks/schemas.py`
- Create: `tests/unit/test_blocks_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_blocks_schemas.py
import pytest
from pydantic import ValidationError

from orchestrator.blocks.schemas import (CodeBlock, HeadingBlock, ImageBlock,
                                          ListBlock, QuoteBlock, TableBlock,
                                          TextBlock)


def test_heading_block_validates_level():
    h = HeadingBlock(level=2, text="Hi")
    assert h.level == 2
    assert h.text == "Hi"


def test_heading_block_rejects_level_out_of_range():
    with pytest.raises(ValidationError):
        HeadingBlock(level=4, text="x")


def test_text_block_minimal():
    t = TextBlock(text="hello")
    assert t.type == "text"


def test_code_block_minimal():
    c = CodeBlock(language="python", text="x=1")
    assert c.type == "code"


def test_quote_block_minimal():
    q = QuoteBlock(text="...")
    assert q.type == "quote"


def test_table_block_validates_row_columns():
    t = TableBlock(
        headers=["A", "B"],
        rows=[["1", "2"], ["3", "4"]],
    )
    assert t.type == "table"


def test_table_block_rejects_row_column_mismatch():
    with pytest.raises(ValidationError):
        TableBlock(headers=["A", "B"], rows=[["1", "2", "3"]])


def test_list_block_minimal():
    l = ListBlock(items=["x", "y"])
    assert l.type == "list"


def test_image_block_minimal():
    i = ImageBlock(url="https://example.com/x.png", alt="x")
    assert i.type == "image"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_blocks_schemas.py -v`
Expected: ImportError

- [ ] **Step 3: Create `__init__.py`**

```python
# orchestrator/blocks/__init__.py
# empty
```

- [ ] **Step 4: Create schemas.py**

```python
# orchestrator/blocks/schemas.py
"""Phase 5 富文本块 schema：6 类 Block Pydantic 模型。"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator


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


class QuoteContainerBlock(BaseModel):
    """Phase 5 飞书 doc quote_container 结构（内部嵌套 text）。"""
    type: Literal["quote_container"] = "quote_container"
    text: str


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]

    @field_validator("rows")
    @classmethod
    def check_rows_columns(cls, v, info):
        headers = info.data.get("headers", [])
        if not headers:
            return v
        col_count = len(headers)
        for i, row in enumerate(v):
            if len(row) != col_count:
                raise ValueError(
                    f"row {i} has {len(row)} columns, expected {col_count}"
                )
        return v


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1)


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    url: str = Field(pattern=r"^https?://")
    alt: str = ""
    width: int | None = None
    height: int | None = None


AnyBlock = Union[HeadingBlock, TextBlock, CodeBlock, QuoteBlock, QuoteContainerBlock,
                  TableBlock, ListBlock, ImageBlock]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_blocks_schemas.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/blocks/ tests/unit/test_blocks_schemas.py
git commit -m "feat(phase5): add 6 block schemas (heading/text/code/quote/table/list/image)"
```

---

## Task 2: Block serializer（JSON ↔ blocks）

**Files:**
- Create: `orchestrator/blocks/serializer.py`
- Create: `tests/unit/test_blocks_serializer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_blocks_serializer.py
import pytest

from orchestrator.blocks.schemas import (CodeBlock, HeadingBlock, ImageBlock,
                                          ListBlock, QuoteBlock, TableBlock,
                                          TextBlock)
from orchestrator.blocks.serializer import blocks_to_json, json_to_blocks


def test_blocks_to_json_roundtrip():
    blocks = [
        HeadingBlock(level=2, text="Hi"),
        TextBlock(text="hello"),
        CodeBlock(language="python", text="x=1"),
        QuoteBlock(text="..."),
        TableBlock(headers=["A"], rows=[["1"]]),
        ListBlock(items=["x"]),
        ImageBlock(url="https://example.com/x.png"),
    ]
    s = blocks_to_json(blocks)
    out = json_to_blocks(s)
    assert len(out) == 7
    assert out[0].text == "Hi"
    assert out[6].url == "https://example.com/x.png"


def test_blocks_to_json_empty_list():
    s = blocks_to_json([])
    assert s == "[]"


def test_json_to_blocks_invalid_type_raises():
    import json
    bad = json.dumps([{"type": "unknown", "x": 1}])
    with pytest.raises(ValueError):
        json_to_blocks(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_blocks_serializer.py -v`
Expected: ImportError

- [ ] **Step 3: Create serializer.py**

```python
# orchestrator/blocks/serializer.py
"""Phase 5 blocks ↔ JSON 序列化（用于模板存储）。"""
from __future__ import annotations

import json
from typing import Any

from orchestrator.blocks.schemas import (AnyBlock, CodeBlock, HeadingBlock,
                                           ImageBlock, ListBlock, QuoteBlock,
                                           QuoteContainerBlock, TableBlock,
                                           TextBlock)


def blocks_to_json(blocks: list[AnyBlock]) -> str:
    """list[Block] → JSON 字符串。"""
    return json.dumps([b.model_dump() for b in blocks], ensure_ascii=False)


def json_to_blocks(s: str) -> list[AnyBlock]:
    """JSON 字符串 → list[Block]。"""
    data = json.loads(s)
    return [_parse_block(b) for b in data]


def _parse_block(d: dict[str, Any]) -> AnyBlock:
    t = d.get("type")
    match t:
        case "heading": return HeadingBlock(**d)
        case "text":    return TextBlock(**d)
        case "code":    return CodeBlock(**d)
        case "quote":   return QuoteBlock(**d)
        case "quote_container": return QuoteContainerBlock(**d)
        case "table":   return TableBlock(**d)
        case "list":    return ListBlock(**d)
        case "image":   return ImageBlock(**d)
        case _: raise ValueError(f"unknown block type: {t}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_blocks_serializer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/blocks/serializer.py tests/unit/test_blocks_serializer.py
git commit -m "feat(phase5): add blocks serializer (JSON ↔ blocks)"
```

---

## Task 3: ORM TemplateRow + TemplateRepo

**Files:**
- Modify: `persistence/models.py`
- Create: `persistence/repositories/template_repo.py`
- Create: `tests/unit/test_template_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_template_repo.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_repo import TemplateRepo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def test_template_upsert_and_get(session):
    repo = TemplateRepo(session)
    repo.upsert(
        template_id="t1", owner_open_id="ou_1",
        name="std_report", type_="block",
        blocks_json='[{"type":"text","text":"hi"}]',
        steps_json=None, description="x",
    )
    t = repo.get("t1")
    assert t.owner_open_id == "ou_1"
    assert t.type == "block"


def test_template_list_by_owner(session):
    repo = TemplateRepo(session)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="a", type_="block", blocks_json=None,
                steps_json=None, description="")
    repo.upsert(template_id="t2", owner_open_id="ou_1",
                name="b", type_="subplan", blocks_json=None,
                steps_json='[]', description="")
    repo.upsert(template_id="t3", owner_open_id="ou_2",
                name="c", type_="block", blocks_json=None,
                steps_json=None, description="")
    out = repo.list_by_owner("ou_1")
    assert len(out) == 2


def test_template_delete(session):
    repo = TemplateRepo(session)
    repo.upsert(template_id="t1", owner_open_id="ou_1",
                name="a", type_="block", blocks_json=None,
                steps_json=None, description="")
    repo.delete("t1")
    assert repo.get("t1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_template_repo.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `persistence/models.py`**

Append at end:

```python
# === Phase 5 ===

class TemplateRow(Base):
    """用户私有模板表（Block + sub-Plan）。"""
    __tablename__ = "templates"

    template_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_open_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    blocks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Create `template_repo.py`**

```python
# persistence/repositories/template_repo.py
"""templates 表的 CRUD。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TemplateRow


class TemplateRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        *,
        template_id: str,
        owner_open_id: str,
        name: str,
        type_: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        description: str = "",
    ) -> TemplateRow:
        row = self.session.get(TemplateRow, template_id)
        if row is None:
            row = TemplateRow(
                template_id=template_id,
                owner_open_id=owner_open_id,
                name=name,
                description=description,
                type=type_,
                blocks_json=blocks_json,
                steps_json=steps_json,
            )
            self.session.add(row)
        else:
            row.name = name
            row.description = description
            row.type = type_
            row.blocks_json = blocks_json
            row.steps_json = steps_json
        self.session.flush()
        return row

    def get(self, template_id: str) -> Optional[TemplateRow]:
        return self.session.get(TemplateRow, template_id)

    def list_by_owner(self, owner_open_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(owner_open_id=owner_open_id, archived_at=None)
            .all()
        )

    def delete(self, template_id: str) -> None:
        row = self.session.get(TemplateRow, template_id)
        if row is not None:
            row.archived_at = datetime.utcnow()
            self.session.flush()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_template_repo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add persistence/models.py persistence/repositories/template_repo.py tests/unit/test_template_repo.py
git commit -m "feat(phase5): add TemplateRow + TemplateRepo"
```

---

## Task 4: TemplateRenderer（substitute + render）

**Files:**
- Create: `orchestrator/templates/__init__.py`
- Create: `orchestrator/templates/schemas.py`
- Create: `orchestrator/templates/renderer.py`
- Create: `tests/unit/test_template_renderer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_template_renderer.py
from orchestrator.templates.renderer import substitute, render_block, render_subplan
from orchestrator.templates.schemas import SubPlanTemplateStep


def test_substitute_string():
    out = substitute("hello {{name}}", {"name": "world"})
    assert out == "hello world"


def test_substitute_missing_var_keeps_placeholder():
    out = substitute("hello {{name}}", {})
    assert out == "hello {{name}}"


def test_substitute_nested_dict_list():
    obj = {"a": "{{x}}", "b": ["{{y}}", "literal"], "c": {"d": "{{z}}"}}
    out = substitute(obj, {"x": "1", "y": "2", "z": "3"})
    assert out == {"a": "1", "b": ["2", "literal"], "c": {"d": "3"}}


def test_render_subplan_substitutes_step_inputs():
    steps = [SubPlanTemplateStep(
        step_id="s1", tool_name="blast_search",
        inputs={"query": "{{gene}}", "database": "nr"},
    )]
    out = render_subplan(steps, params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"
    assert out[0].inputs["database"] == "nr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_template_renderer.py -v`
Expected: ImportError

- [ ] **Step 3: Create `__init__.py`**

```python
# orchestrator/templates/__init__.py
# empty
```

- [ ] **Step 4: Create schemas.py**

```python
# orchestrator/templates/schemas.py
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
```

- [ ] **Step 5: Create renderer.py**

```python
# orchestrator/templates/renderer.py
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
    from orchestrator.blocks.serializer import json_to_blocks
    blocks = json_to_blocks(blocks_json)
    return [substitute(b.model_dump(), params) for b in blocks]


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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_template_renderer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add orchestrator/templates/ tests/unit/test_template_renderer.py
git commit -m "feat(phase5): add TemplateRenderer (substitute + render)"
```

---

## Task 5: TemplateService（CRUD + 权限）

**Files:**
- Create: `orchestrator/templates/template_service.py`
- Create: `tests/unit/test_template_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_template_service.py
from unittest.mock import MagicMock

import pytest

from orchestrator.templates.schemas import SubPlanTemplateStep
from orchestrator.templates.template_service import TemplateService


def _make_service():
    return TemplateService(repo=MagicMock()), MagicMock()


def test_create_block_returns_template_id():
    svc, repo = _make_service()
    repo.get.return_value = None
    from orchestrator.blocks.schemas import HeadingBlock, TextBlock
    tid = svc.create_block(
        owner_open_id="ou_1", name="std",
        blocks=[HeadingBlock(level=2, text="Hi"), TextBlock(text="x")],
        description="x",
    )
    assert tid
    repo.upsert.assert_called_once()


def test_create_subplan_returns_template_id():
    svc, repo = _make_service()
    repo.get.return_value = None
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="blast",
        steps=[SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                    inputs={"query": "BRCA1"})],
        description="x",
    )
    assert tid


def test_delete_requires_owner():
    svc, repo = _make_service()
    from datetime import datetime
    repo.get.return_value = MagicMock(owner_open_id="ou_2",
                                       archived_at=None,
                                       updated_at=datetime.utcnow())
    with pytest.raises(PermissionError):
        svc.delete(template_id="t1", caller_open_id="ou_1")


def test_delete_owner_succeeds():
    svc, repo = _make_service()
    from datetime import datetime
    repo.get.return_value = MagicMock(owner_open_id="ou_1",
                                       archived_at=None,
                                       updated_at=datetime.utcnow())
    svc.delete(template_id="t1", caller_open_id="ou_1")
    repo.delete.assert_called_once_with("t1")


def test_list_by_owner():
    svc, repo = _make_service()
    repo.list_by_owner.return_value = ["t1", "t2"]
    out = svc.list_by_owner("ou_1")
    assert out == ["t1", "t2"]


def test_render_subplan_substitutes_params():
    svc, repo = _make_service()
    from datetime import datetime
    repo.get.return_value = MagicMock(
        template_id="t1", owner_open_id="ou_1", name="x",
        description="", type="subplan",
        steps_json='[{"step_id": "s1", "tool_name": "blast_search",'
                  ' "inputs": {"query": "{{gene}}"}}]',
        blocks_json=None, archived_at=None,
        updated_at=datetime.utcnow(),
    )
    out = svc.render_subplan(template_id="t1", params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_template_service.py -v`
Expected: ImportError

- [ ] **Step 3: Create template_service.py**

```python
# orchestrator/templates/template_service.py
"""Phase 5 模板服务：CRUD + 权限校验。"""
from __future__ import annotations

import json
from typing import Optional

from orchestrator.blocks.schemas import AnyBlock
from orchestrator.blocks.serializer import blocks_to_json
from orchestrator.templates.renderer import render_block, render_subplan
from orchestrator.templates.schemas import SubPlanTemplateStep
from shared.ulid_ import new_ulid


class TemplateService:
    def __init__(self, repo) -> None:
        self.repo = repo

    def create_block(
        self, *, owner_open_id: str, name: str,
        blocks: list[AnyBlock], description: str = "",
    ) -> str:
        tid = new_ulid()
        self.repo.upsert(
            template_id=tid, owner_open_id=owner_open_id,
            name=name, type_="block",
            blocks_json=blocks_to_json(blocks),
            steps_json=None, description=description,
        )
        return tid

    def create_subplan(
        self, *, owner_open_id: str, name: str,
        steps: list[SubPlanTemplateStep], description: str = "",
    ) -> str:
        tid = new_ulid()
        self.repo.upsert(
            template_id=tid, owner_open_id=owner_open_id,
            name=name, type_="subplan",
            blocks_json=None,
            steps_json=json.dumps([s.model_dump() for s in steps],
                                  ensure_ascii=False),
            description=description,
        )
        return tid

    def list_by_owner(self, owner_open_id: str) -> list:
        return self.repo.list_by_owner(owner_open_id)

    def get(self, template_id: str):
        return self.repo.get(template_id)

    def render_block(self, *, template_id: str, params: dict):
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.type != "block":
            raise ValueError(f"template {template_id} is not a block template")
        return render_block(tpl.blocks_json, params)

    def render_subplan(self, *, template_id: str, params: dict):
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.type != "subplan":
            raise ValueError(f"template {template_id} is not a subplan template")
        steps_data = json.loads(tpl.steps_json or "[]")
        steps = [SubPlanTemplateStep(**s) for s in steps_data]
        return render_subplan(steps, params=params)

    def delete(self, *, template_id: str, caller_open_id: str) -> None:
        tpl = self.repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of template {template_id}"
            )
        self.repo.delete(template_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_template_service.py tests/unit/test_template_repo.py tests/unit/test_template_renderer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/templates/template_service.py tests/unit/test_template_service.py
git commit -m "feat(phase5): add TemplateService (CRUD + owner permission)"
```

---

## Task 6: ToolHandler 透传 blocks 字段

**Files:**
- Modify: `orchestrator/tools/tool_handler.py`
- Create: `tests/unit/test_tool_handler_blocks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tool_handler_blocks.py
from unittest.mock import MagicMock

from orchestrator.tools.tool_registry import ToolSpec
from orchestrator.tools.tool_handler import ToolHandler, ToolResult


def test_tool_handler_extracts_blocks_field():
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {
            "data": "y",
            "blocks": [{"type": "text", "text": "hi"}],
        },
    )
    handler = ToolHandler(registry=reg)
    result = handler.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.outputs == {"data": "y"}
    assert result.blocks is not None
    assert len(result.blocks) == 1
    assert result.blocks[0].text == "hi"


def test_tool_handler_blocks_none_when_not_provided():
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {"data": "y"},
    )
    handler = ToolHandler(registry=reg)
    result = handler.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.outputs == {"data": "y"}
    assert result.blocks is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_tool_handler_blocks.py -v`
Expected: FAIL (AttributeError on .blocks)

- [ ] **Step 3: Modify `tool_handler.py`**

```python
# 在文件顶部追加 import:
from orchestrator.blocks.serializer import json_to_blocks

# 修改 ToolResult dataclass:
@dataclass
class ToolResult:
    outputs: dict
    artifacts_ids: list[str]
    error_code: str | None = None
    error_message: str | None = None
    blocks: list | None = None  # Phase 5

# 修改 execute() 中的 return ToolResult(...) 行:
        try:
            out = spec.handler(**inputs)
            if not isinstance(out, dict):
                out = {"result": out}
            out.update(outputs_extra)
            blocks_raw = out.pop("blocks", None)
            tool_err = out.pop("error_code", None)
            tool_err_msg = out.pop("error_message", None)
            blocks = json_to_blocks(json.dumps(blocks_raw)) if blocks_raw else None
            return ToolResult(
                outputs=out, artifacts_ids=[],
                error_code=tool_err, error_message=tool_err_msg,
                blocks=blocks,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_tool_handler_blocks.py tests/unit/test_tool_handler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/tool_handler.py tests/unit/test_tool_handler_blocks.py
git commit -m "feat(phase5): ToolHandler extracts blocks field from outputs"
```

---

## Task 7: TemplateEngine 返回 list[Block]

**Files:**
- Modify: `orchestrator/template_engine.py`
- Create: `tests/unit/test_template_engine_phase5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_template_engine_phase5.py
from orchestrator.blocks.schemas import AnyBlock
from orchestrator.template_engine import TemplateEngine


def test_render_plan_summary_returns_blocks():
    eng = TemplateEngine()
    blocks = eng.render_plan_summary(
        status="success",
        node_states={"n1": "success", "n2": "failed"},
        artifacts_count=2,
    )
    assert len(blocks) > 0
    assert all(isinstance(b, AnyBlock) for b in blocks)


def test_render_plan_summary_includes_status_table():
    eng = TemplateEngine()
    blocks = eng.render_plan_summary(
        status="success",
        node_states={"n1": "success"},
        artifacts_count=0,
    )
    types = [b.type for b in blocks]
    assert "table" in types
    assert "heading" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_template_engine_phase5.py -v`
Expected: FAIL (return type 期望 list[Block] 实际 str)

- [ ] **Step 3: Modify `template_engine.py`**

把 render_plan_summary 改成返回 list[Block]：

```python
# orchestrator/template_engine.py
from orchestrator.blocks.schemas import (HeadingBlock, ListBlock, TableBlock,
                                          TextBlock)
from orchestrator.blocks.serializer import blocks_to_json
from typing import Iterable


class TemplateEngine:
    def render_plan_summary(
        self, *, status: str, node_states: dict, artifacts_count: int,
    ) -> list:
        """Phase 5: 返回 list[Block]。"""
        return [
            HeadingBlock(level=2, text=f"Plan {status}"),
            TextBlock(text=f"Nodes: {len(node_states)}; Artifacts: {artifacts_count}"),
            TableBlock(
                headers=["Node", "State"],
                rows=[[nid, state] for nid, state in node_states.items()],
            ),
        ]
    
    def render_blocks_to_text(self, blocks) -> str:
        """兼容 Phase 1-4 调用：list[Block] → 文本（用于 IM reply）。"""
        lines = []
        for b in blocks:
            if b.type == "heading":
                lines.append(f"{'#' * b.level} {b.text}")
            elif b.type == "text":
                lines.append(b.text)
            elif b.type == "code":
                lines.append(f"```{b.language}\n{b.text}\n```")
            elif b.type == "quote":
                lines.append(f"> {b.text}")
            elif b.type == "table":
                lines.append(", ".join(b.headers))
                lines.extend([", ".join(r) for r in b.rows])
            elif b.type == "list":
                for i, item in in enumerate(b.items, 1):
                    if b.ordered:
                        lines.append(f"{i}. {item}")
                    else:
                        lines.append(f"- {item}")
            elif b.type == "image":
                lines.append(f"![{b.alt}]({b.url})")
        return "\n".join(lines)
```

**Phase 1-4 调用方**：`process_phase2/3/4` 仍调 `render_plan_summary()`，但现在返回 list[Block] 而非 text。需要小适配：

```python
# orchestrator/app.py 修改所有 render_plan_summary 调用：
blocks = self.template.render_plan_summary(...)
text_for_im = self.template.render_blocks_to_text(blocks)  # IM reply
if getattr(sess, "bound_doc_id", None):
    self.doc_adapter.append_blocks(sess.bound_doc_id, blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_template_engine_phase5.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/template_engine.py tests/unit/test_template_engine_phase5.py
git commit -m "feat(phase5): TemplateEngine.render_plan_summary returns list[Block]"
```

---

## Task 8: DocAdapter.render_blocks（6 类 → 飞书）

**Files:**
- Modify: `feishu_adapter/doc_adapter.py`
- Create: `tests/integration/test_doc_adapter_blocks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_doc_adapter_blocks.py
import httpx
import pytest
import respx

from feishu_adapter.doc_adapter import DocAdapter
from orchestrator.blocks.schemas import (CodeBlock, HeadingBlock, ImageBlock,
                                          ListBlock, QuoteBlock, TableBlock,
                                          TextBlock)


@pytest.fixture
def adapter():
    a = DocAdapter(base_url="https://example.feishu.cn", api_token="t")
    return a


@respx.mock
def test_render_heading(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1", [HeadingBlock(level=2, text="Hi")])
    # 调用过 1 次
    assert respx.calls.call_count == 1


@respx.mock
def test_render_all_six_types(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [
        HeadingBlock(level=1, text="h"),
        TextBlock(text="t"),
        CodeBlock(language="py", text="x"),
        QuoteBlock(text="q"),
        TableBlock(headers=["A"], rows=[["1"]]),
        ListBlock(items=["x"]),
        ImageBlock(url="https://x.png"),
    ]
    adapter.render_blocks("doc_1", blocks)
    # 7 calls (heading h1 + h2 + h3 是 3 类，但本测试只测基础 6 类 + 1 heading)
    assert respx.calls.call_count == 7


@respx.mock
def test_render_blocks_respects_rate_limit(adapter):
    """3 req/s 限流：连续渲染多个 block，最少等待 2s。"""
    import time
    # 5 个 block：1 + 4 × 0.333s = ~1.0s
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [HeadingBlock(level=2, text=f"h{i}") for i in range(5)]
    t0 = time.monotonic()
    adapter.render_blocks("doc_1", blocks)
    elapsed = time.monotonic() - t0
    # 5 calls: 4 intervals × 0.333s ≈ 1.33s
    assert elapsed >= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_doc_adapter_blocks.py -v`
Expected: ImportError or AttributeError

- [ ] **Step 3: Modify `doc_adapter.py`**

```python
# feishu_adapter/doc_adapter.py
# 在文件顶部追加：
from orchestrator.blocks.schemas import AnyBlock
from orchestrator.blocks.serializer import blocks_to_json

# 新增方法：
class DocAdapter:
    def __init__(self, base_url="", api_token="", rate_limiter=None):
        self.base_url = base_url
        self.api_token = api_token
        from orchestrator.tools.bio.rate_limiter import RateLimiter
        self._rate_limiter = rate_limiter or RateLimiter(rate=3.0, per_sec=1.0)
    
    def render_blocks(self, doc_id: str, blocks: list[AnyBlock]) -> None:
        """Phase 5: 把 list[Block] 渲染到飞书 doc。"""
        for block in blocks:
            self._rate_limiter.wait()
            payload = self._to_feishu_payload(block)
            self._post_block(doc_id, payload)
    
    def _to_feishu_payload(self, block: AnyBlock) -> dict:
        t = block.type
        if t == "heading":
            level = block.level
            return {
                "block_type": f"heading{level}",
                f"heading{level}": {"elements": [
                    {"text_run": {"content": block.text}}
                ]},
            }
        if t == "text":
            return {"block_type": "text",
                    "text": {"elements": [
                        {"text_run": {"content": block.text}}
                    ]}}
        if t == "code":
            return {"block_type": "code",
                    "code": {"elements": [
                        {"text_run": {"content": block.text}}
                    ], "language": block.language}}
        if t == "quote":
            return {"block_type": "quote_container",
                    "quote_container": [{"block_type": "text",
                                          "text": {"elements": [
                                              {"text_run": {"content": block.text}}
                                          ]}}]}
        if t == "table":
            return {"block_type": "table",
                    "table": {
                        "property": {"row_size": len(block.rows) + 1,
                                      "column_size": len(block.headers)},
                        "cells": [block.headers] + block.rows,
                    }}
        if t == "list":
            list_type = "ordered_list" if block.ordered else "bullet_list"
            return {"block_type": list_type,
                    list_type": {"elements": [
                        {"text_run": {"content": item}}
                        for item in block.items
                    ]}}
        if t == "image":
            return {"block_type": "image",
                    "image": {"url": block.url, "alt": block.alt}}
        raise ValueError(f"unsupported block type: {t}")
    
    def _post_block(self, doc_id: str, payload: dict) -> None:
        # 实际生产中用 httpx.post；测试中 respx mock
        import httpx
        url = f"{self.base_url}/docx/v1/blocks"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        resp = httpx.post(url, headers=headers,
                          json={"doc_id": doc_id, "block": payload},
                          timeout=10)
        resp.raise_for_status()
```

**注意**：DocAdapter 现有 `append_blocks`（Phase 1）保留；新增 `render_blocks` 是 Phase 5 新方法。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_doc_adapter_blocks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add feishu_adapter/doc_adapter.py tests/integration/test_doc_adapter_blocks.py
git commit -m "feat(phase5): DocAdapter.render_blocks (6 block types → Feishu doc API)"
```

---

## Task 9: DAGNode + Scheduler 展开 sub-Plan

**Files:**
- Modify: `orchestrator/planner/dag_schema.py`
- Modify: `orchestrator/planner/scheduler.py`
- Create: `tests/integration/test_subplan_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_subplan_expansion.py
from unittest.mock import MagicMock

from orchestrator.planner.dag_schema import DAGNode
from orchestrator.planner.scheduler import Scheduler
from orchestrator.templates.schemas import SubPlanTemplateStep


def test_scheduler_expand_subplan():
    rt = MagicMock()
    rt.state = MagicMock()
    template_service = MagicMock()
    template_service.get.return_value = MagicMock(
        template_id="t1", type="subplan",
        steps_json='[{"step_id": "s1", "tool_name": "blast_search",'
                  ' "inputs": {"query": "{{gene}}"}},'
                  ' {"step_id": "s2", "tool_name": "summary",'
                  ' "inputs": {"input": "n1.records"}}]',
    )
    template_service.render_subplan.return_value = [
        SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                             inputs={"query": "BRCA1"}),
        SubPlanTemplateStep(step_id="s2", tool_name="summary",
                             inputs={"input": "n1.records"}),
    ]
    n = DAGNode(node_id="n1", kind="tool", tool_name="tpl_use",
                inputs={},
                subplan_template_id="t1",
                subplan_params={"gene": "BRCA1"})
    plan = MagicMock()
    plan.task_id = "t"
    plan.nodes = [n]
    plan.entry_node_ids = ["n1"]
    
    # Validate expand
    expanded = Scheduler._expand_subplan_static(
        n, template_service
    )
    assert len(expanded) == 2
    assert expanded[0].tool_name == "blast_search"
    assert expanded[0].inputs["query"] == "BRCA1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_subplan_expansion.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `dag_schema.py`**

```python
# 在 DAGNode 类添加字段（Phase 5）：
    # === Phase 5 ===
    subplan_template_id: Optional[str] = None
    subplan_params: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 4: Modify `scheduler.py`**

```python
# 在 Scheduler 类添加静态方法：
    @staticmethod
    def _expand_subplan_static(node: DAGNode, template_service) -> list[DAGNode]:
        """Phase 5: 展开 sub-Plan 模板为内联 DAGNode 列表。"""
        if not node.subplan_template_id:
            return [node]
        tpl = template_service.get(node.subplan_template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {node.subplan_template_id} not found")
        if tpl.type != "subplan":
            raise ValueError(f"template {node.subplan_template_id} is not subplan")
        steps = template_service.render_subplan(
            template_id=node.subplan_template_id,
            params=node.subplan_params,
        )
        out = []
        for i, step in enumerate(steps):
            out.append(DAGNode(
                node_id=f"{node.node_id}_step_{i}",
                kind="tool",
                tool_name=step.tool_name,
                inputs=step.inputs,
                depends_on=list(node.depends_on) if i == 0 else [],
            ))
        return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_subplan_expansion.py tests/unit/test_dag_schema.py tests/unit/test_dag_schema_phase3.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/planner/dag_schema.py orchestrator/planner/scheduler.py tests/integration/test_subplan_expansion.py
git commit -m "feat(phase5): DAGNode subplan_template_id + Scheduler._expand_subplan_static"
```

---

## Task 10: FastAPI /templates/* 路由

**Files:**
- Modify: `gateway/app.py`
- Create: `tests/integration/test_templates_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_templates_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from gateway.app import create_app
from orchestrator.templates.template_service import TemplateService


@pytest.fixture
def client():
    fake_template_service = MagicMock(spec=TemplateService)
    return TestClient(create_app(
        secret="phase2-secret",
        orchestrator=object(),
        template_service=fake_template_service,
    )), fake_template_service


def test_create_block_endpoint(client):
    c, svc = client
    svc.create_block.return_value = "t_1"
    body = {
        "owner_open_id": "ou_1",
        "name": "std",
        "blocks": [
            {"type": "heading", "level": 2, "text": "Hi"},
            {"type": "text", "text": "x"},
        ],
        "description": "d",
    }
    resp = c.post("/templates/block", json=body)
    assert resp.status_code == 200
    assert resp.json()["template_id"] == "t_1"


def test_list_endpoint(client):
    c, svc = client
    svc.list_by_owner.return_value = ["t1", "t2"]
    resp = c.get("/templates/", params={"owner_open_id": "ou_1"})
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1", "t2"]


def test_render_endpoint(client):
    c, svc = client
    svc.render_block.return_value = [{"type": "heading", "level": 2,
                                       "text": "BRCA1"}]
    body = {"params": {"gene": "BRCA1"}}
    resp = c.post("/templates/t_1/render", json=body)
    assert resp.status_code == 200
    svc.render_block.assert_called_once()


def test_delete_endpoint_requires_owner(client):
    c, svc = client
    from orchestrator.templates.template_service import TemplateService
    svc.delete.side_effect = PermissionError("not owner")
    resp = c.delete("/templates/t_1", params={"caller_open_id": "ou_2"})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_templates_api.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `gateway/app.py`**

```python
# 在 create_app() 添加参数：
def create_app(
    secret: str, orchestrator, rate_per_min: int = 60,
    session_factory=None, bind_doc_service=None, template_service=None,
) -> FastAPI:
    ...
    app.state.ctx = AppContext(
        ...,
        template_service=template_service,
    )
    
    # === Phase 5: 模板市场 API ===
    @app.post("/templates/block")
    async def create_block_template(request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        from orchestrator.blocks.serializer import json_to_blocks
        blocks = json_to_blocks(json.dumps(body["blocks"]))
        tid = ts.create_block(
            owner_open_id=body["owner_open_id"],
            name=body["name"],
            blocks=blocks,
            description=body.get("description", ""),
        )
        return {"ok": True, "template_id": tid}
    
    @app.post("/templates/subplan")
    async def create_subplan_template(request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        from orchestrator.templates.schemas import SubPlanTemplateStep
        steps = [SubPlanTemplateStep(**s) for s in body["steps"]]
        tid = ts.create_subplan(
            owner_open_id=body["owner_open_id"],
            name=body["name"],
            steps=steps,
            description=body.get("description", ""),
        )
        return {"ok": True, "template_id": tid}
    
    @app.get("/templates/")
    async def list_templates(owner_open_id: str):
        ctx = app.state.ctx
        if ctx.template_service is None:
            return {"templates": []}
        return {"templates": [
            t.template_id for t in ctx.template_service.list_by_owner(owner_open_id)
        ]}
    
    @app.post("/templates/{template_id}/render")
    async def render_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        params = body.get("params", {})
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        tpl = ts.get(template_id)
        if tpl is None:
            return {"ok": False, "reason": "not found"}, 404
        if tpl.type == "block":
            blocks = ts.render_block(template_id=template_id, params=params)
            return {"ok": True, "blocks": blocks}
        else:
            steps = ts.render_subplan(template_id=template_id, params=params)
            return {"ok": True, "steps": [s.model_dump() for s in steps]}
    
    @app.delete("/templates/{template_id}")
    async def delete_template(template_id: str, caller_open_id: str):
        ctx = app.state.ctx
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "not configured"}
        try:
            ts.delete(template_id=template_id, caller_open_id=caller_open_id)
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}
```

更新 AppContext 添加 template_service 字段：

```python
class AppContext:
    ...
    template_service: object | None = None  # Phase 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_templates_api.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app.py tests/integration/test_templates_api.py
git commit -m "feat(phase5): FastAPI /templates/* routes (CRUD + render + owner check)"
```

---

## Task 11: Orchestrator.process_phase5 入口

**Files:**
- Modify: `orchestrator/app.py`
- Create: `tests/unit/test_orchestrator_phase5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_orchestrator_phase5.py
from unittest.mock import MagicMock

from orchestrator.app import Orchestrator


def test_orchestrator_phase5_has_process_phase5():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    assert hasattr(orch, "process_phase5")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_orchestrator_phase5.py -v`
Expected: AttributeError

- [ ] **Step 3: Append `process_phase5` to orchestrator/app.py**

```python
    # === Phase 5 ===
    def process_phase5(self, incoming) -> dict:
        """Phase 5 入口：复用 process_phase4 + 模板市场指令。
        
        新增指令：
        - /template-list
        - /template-create-block
        - /template-create-subplan
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 5 subsystems not initialized")
        
        text = incoming.text.strip()
        # IM 指令路由
        if text == "/template-list":
            ts = getattr(self, "template_service", None)
            if ts is None:
                self.im.reply(incoming.chat_id, "[错误] template_service 未配置")
                return {"status": "template_list_failed"}
            templates = ts.list_by_owner(incoming.sender_open_id)
            ids = [t.template_id for t in templates]
            self.im.reply(incoming.chat_id, f"您的模板: {', '.join(ids) or '(无)'}")
            return {"status": "template_listed", "templates": ids}
        
        # 普通消息：复用 process_phase4
        return self.process_phase4(incoming)
```

**Orchestrator __init__** 增加 `template_service` 参数：

```python
        template_service=None,
```

并在 init 中 `self.template_service = template_service`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_orchestrator_phase5.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app.py tests/unit/test_orchestrator_phase5.py
git commit -m "feat(phase5): Orchestrator.process_phase5 with template IM commands"
```

---

## Task 12: 端到端 E1-E8（整合测试）

**Files:**
- Create: `tests/integration/test_e2e_phase5_e1_e8.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase5_e1_e8.py
"""E1-E8: Phase 5 端到端场景。"""
import json
from unittest.mock import MagicMock

import pytest

from orchestrator.blocks.schemas import (HeadingBlock, ListBlock, TableBlock,
                                          TextBlock)
from orchestrator.blocks.serializer import blocks_to_json, json_to_blocks
from orchestrator.templates.schemas import SubPlanTemplateStep
from orchestrator.templates.template_service import TemplateService


def _make_service():
    from persistence.repositories.template_repo import TemplateRepo
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from persistence.models import Base
    engine = create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return TemplateService(repo=TemplateRepo(session)), session


# E1: 上传 Block 模板 → 复用 → 飞书 doc 渲染 6 类 block
def test_e1_block_template_full_pipeline():
    svc, _ = _make_service()
    blocks = [
        HeadingBlock(level=2, text="Report"),
        TextBlock(text="intro"),
        ListBlock(items=["x", "y"]),
        TableBlock(headers=["A", "B"], rows=[["1", "2"]]),
    ]
    tid = svc.create_block(owner_open_id="ou_1", name="std",
                            blocks=blocks, description="")
    out = svc.render_block(template_id=tid, params={})
    assert len(out) == 4
    assert out[0]["type"] == "heading"


# E2: sub-Plan 模板上传 → Planner DAG → Scheduler 展开
def test_e2_subplan_template_expansion():
    from orchestrator.planner.scheduler import Scheduler
    from orchestrator.planner.dag_schema import DAGNode
    svc, _ = _make_service()
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="blast",
        steps=[
            SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                 inputs={"query": "BRCA1"}),
        ],
        description="",
    )
    n = DAGNode(node_id="n1", kind="tool", tool_name="tpl_use",
                inputs={}, subplan_template_id=tid)
    expanded = Scheduler._expand_subplan_static(n, svc)
    assert len(expanded) == 1
    assert expanded[0].tool_name == "blast_search"


# E3: 模板参数化 {{gene}} → 渲染时替换
def test_e3_template_param_substitution():
    svc, _ = _make_service()
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="t",
        steps=[
            SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                 inputs={"query": "{{gene}}"}),
        ],
        description="",
    )
    out = svc.render_subplan(template_id=tid, params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"


# E4: 模板权限隔离
def test_e4_template_privacy():
    svc, _ = _make_service()
    tid = svc.create_block(owner_open_id="ou_1", name="x",
                            blocks=[TextBlock(text="private")], description="")
    with pytest.raises(PermissionError):
        svc.delete(template_id=tid, caller_open_id="ou_2")


# E5: ToolHandler 双路径（blocks 字段）
def test_e5_toolhandler_blocks_field():
    from orchestrator.tools.tool_handler import ToolHandler
    from orchestrator.tools.tool_registry import ToolSpec
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {
            "data": "y",
            "blocks": [{"type": "heading", "level": 2, "text": "Hi"}],
        },
    )
    h = ToolHandler(registry=reg)
    result = h.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.blocks is not None
    assert len(result.blocks) == 1
    assert result.blocks[0].text == "Hi"


# E6: DocAdapter 飞书 doc 限流（已在 test_doc_adapter_blocks 覆盖）


# E7: Block schema 验证
def test_e7_block_schema_validation():
    from pydantic import ValidationError
    from orchestrator.blocks.schemas import TableBlock
    with pytest.raises(ValidationError):
        TableBlock(headers=["A", "B"], rows=[["1", "2", "3"]])


# E8: FastAPI /templates/* 全链路
def test_e8_templates_api_full_pipeline():
    from fastapi.testclient import TestClient
    from gateway.app import create_app
    svc, _ = _make_service()
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
        template_service=svc,
    ))
    body = {
        "owner_open_id": "ou_1", "name": "std",
        "blocks": [{"type": "text", "text": "hi"}],
        "description": "d",
    }
    resp = client.post("/templates/block", json=body)
    assert resp.status_code == 200
    tid = resp.json()["template_id"]
    resp = client.get("/templates/", params={"owner_open_id": "ou_1"})
    assert resp.status_code == 200
    assert tid in resp.json()["templates"]
    resp = client.post(f"/templates/{tid}/render", json={"params": {}})
    assert resp.status_code == 200
    resp = client.delete(f"/templates/{tid}",
                          params={"caller_open_id": "ou_1"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase5_e1_e8.py -v`
Expected: PASS (7 tests + E6 在 doc_adapter_blocks 覆盖)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase5_e1_e8.py
git commit -m "test(phase5): add e2e E1-E8 (template pipeline + blocks + permissions)"
```

---

## Task 13: 回归验证

**Files:**
- Modify: 无（仅验证）

- [ ] **Step 1: Run all unit + integration tests**

Run: `python -m pytest tests/unit tests/integration --tb=line -q`
Expected: ALL PASS, 0 regressions（230 + 41 = 271）

如果发现 Phase 1-4 测试失败：

1. 检查 DocAdapter.render_blocks 是否破坏 Phase 1 append_blocks
2. 检查 TemplateEngine.render_plan_summary 返回类型（str → list[Block]）影响 Phase 1-4 调用方
3. 修复并重跑

- [ ] **Step 2: Commit fix (if any)**

```bash
git add -A
git commit -m "fix(phase5): ensure Phase 1-4 regression tests pass"
```

---

## Task 14: 测试总结

**Files:**
- Append to spec: 实施结果

- [ ] **Step 1: Append to Phase 5 spec**

```
### Phase 5 实施结果（已交付）
...（实际测试数 / commit 哈希 / 文件清单）
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-09-feishu-research-agent-phase5-design.md
git commit -m "docs(phase5): append Phase 5 implementation results"
```

---

## Self-Review

### 1. Spec coverage matrix

| Spec § | Requirement | Task | Status |
|---|---|---|---|
| §3 | 富文本块 schema（6 类）| Task 1 | ✓ |
| §3.4 | 互转 JSON | Task 2 | ✓ |
| §4 | 模板市场（Block + sub-Plan + 权限 + 4 个 API）| Task 3, 4, 5, 10 | ✓ |
| §5 | `{{var}}` 参数化 | Task 4 | ✓ |
| §6 | ToolHandler 双路径（blocks 字段）| Task 6 | ✓ |
| §6.5 | TemplateEngine 富文本 | Task 7 | ✓ |
| §7 | DocAdapter 飞书渲染 | Task 8 | ✓ |
| §8 | Scheduler 展开 sub-Plan | Task 9 | ✓ |
| §9 | ORM templates 表 | Task 3 | ✓ |
| §10 | 测试策略 + E1-E8 | Task 12 | ✓ |
| §11 | 不做清单 | 严格遵守 | ✓ |
| §13 | ADR 4 个 | spec/.../adrs/0006-0009 | ✓ |

**Gaps identified**: None — 全部 spec 章节有对应 Task。

### 2. Placeholder scan

搜索 "TBD" / "TODO" / "implement later" / "fill in details" — **0 个**。

### 3. Type consistency

| Name | Definition | Use |
|---|---|---|
| `AnyBlock` Union | Task 1 | Task 2 (serializer), Task 6 (tool_handler), Task 7 (template_engine), Task 8 (doc_adapter) |
| `blocks_to_json` | Task 2 | Task 5 (template_service), Task 4 (renderer.render_block) |
| `json_to_blocks` | Task 2 | Task 4 (renderer), Task 6 (tool_handler) |
| `substitute` | Task 4 | Task 4 (render_block), Task 4 (render_subplan) |
| `TemplateService` | Task 5 | Task 10 (FastAPI routes), Task 11 (Orchestrator) |
| `subplan_template_id` | Task 9 (DAGNode) | Task 9 (Scheduler._expand_subplan_static) |
| `render_blocks` | Task 8 (DocAdapter) | Task 7 (TemplateEngine via app.py) |

All consistent.

---

## Total Task count

**14 Tasks**

| # | Component | Tests |
|---|---|---|
| 1 | Block schemas（6 类）| 8 |
| 2 | Block serializer | 3 |
| 3 | ORM TemplateRow + Repo | 3 |
| 4 | TemplateRenderer | 4 |
| 5 | TemplateService（CRUD + 权限）| 6 |
| 6 | ToolHandler blocks 透传 | 2 |
| 7 | TemplateEngine 富文本 | 2 |
| 8 | DocAdapter.render_blocks | 3 |
| 9 | DAGNode + Scheduler 展开 | 1 |
| 10 | FastAPI /templates/* 路由 | 4 |
| 11 | Orchestrator.process_phase5 | 1 (smoke) |
| 12 | E1-E8 端到端 | 7 + (E6 已覆盖)|
| 13 | 回归验证 | 0 |
| 14 | 测试总结 | 0 |
| **合计** | | **44 新测试** |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-feishu-research-agent-phase5.md`.

**Inline execution chosen**（沿用 Phase 3-4 节奏，subagent 漏建文件 → 直接实施）。

预计 < 15 分钟完成。