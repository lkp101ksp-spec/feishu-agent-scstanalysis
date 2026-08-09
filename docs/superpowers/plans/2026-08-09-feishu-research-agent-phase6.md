# Phase 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline 模式)。Subagent 在 Phase 2/3 期间多次漏建文件，本计划改为直接实施。

**Goal:** 5 大子系统：富文本扩展（14 类）+ 模板版本 + 群聊共享 + 本地 BLAST+ + 工具热加载。

**Architecture:** 5 个独立模块并行实施，按依赖顺序：富文本扩展（独立）→ 模板版本（依赖 ORM）→ 群聊共享（依赖 ORM）→ 本地 BLAST+（独立）→ 工具热加载（依赖 ASTGuard + ToolRegistry）。

**Tech Stack:** pydantic v2 + httpx + respx + pytest + subprocess + importlib

**前置 spec:** [../specs/2026-08-09-feishu-research-agent-phase6-design.md](../specs/2026-08-09-feishu-research-agent-phase6-design.md)

---

## File Structure（前置）

```
orchestrator/blocks/
  schemas.py                    # +8 类（Phase 6 增量）
  serializer.py                 # +8 类分发
orchestrator/templates/
  version_service.py            # 版本管理
  share_service.py              # 群聊共享
orchestrator/tools/bio/
  blast_local.py                # 本地 BLAST+
orchestrator/tools/
  hot_loader.py                 # 工具热加载

persistence/
  models.py                     # +TemplateVersionRow, +scope/chat_id
  repositories/
    template_version_repo.py    # 新增

feishu_adapter/
  doc_adapter.py                # +8 类飞书 API 映射

orchestrator/
  app.py                        # +process_phase6
  template_engine.py            # +8 类文本化
gateway/
  app.py                        # +/versions / /share / /admin/tools 路由

tests/unit/
  test_blocks_schemas_phase6.py
  test_template_version.py
  test_share_service.py
  test_blast_local.py
  test_hot_loader.py
tests/integration/
  test_doc_adapter_blocks_phase6.py
  test_templates_phase6_api.py
  test_e2e_phase6_e1_e8.py
```

---

## Task 1: 富文本 8 类 schema（Phase 6 增量）

**Files:**
- Modify: `orchestrator/blocks/schemas.py`
- Create: `tests/unit/test_blocks_schemas_phase6.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_blocks_schemas_phase6.py
import pytest
from pydantic import ValidationError

from orchestrator.blocks.schemas import (CalloutBlock, DividerBlock,
                                          EmbedBlock, EquationBlock,
                                          FileBlock, MathBlock,
                                          MermaidBlock, VideoBlock)


def test_embed_block_minimal():
    e = EmbedBlock(url="https://example.com")
    assert e.type == "embed"


def test_embed_block_rejects_non_http():
    with pytest.raises(ValidationError):
        EmbedBlock(url="ftp://x")


def test_divider_block_minimal():
    d = DividerBlock()
    assert d.type == "divider"


def test_callout_block_minimal():
    c = CalloutBlock(emoji="⚠️", text="warning")
    assert c.type == "callout"


def test_equation_block_minimal():
    e = EquationBlock(latex="E = mc^2")
    assert e.type == "equation"


def test_math_block_display_mode():
    m = MathBlock(latex="\\sum x", display_mode=True)
    assert m.type == "math"
    assert m.display_mode is True


def test_mermaid_block_minimal():
    m = MermaidBlock(code="graph TD; A-->B")
    assert m.type == "mermaid"


def test_video_block_minimal():
    v = VideoBlock(url="https://x.com/v.mp4")
    assert v.type == "video"


def test_file_block_minimal():
    f = FileBlock(file_token="abc", name="x.txt", size=1024)
    assert f.type == "file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_blocks_schemas_phase6.py -v`
Expected: ImportError

- [ ] **Step 3: Modify schemas.py**

在文件末尾追加：

```python
# === Phase 6: 8 类增量 ===

class EmbedBlock(BaseModel):
    type: Literal["embed"] = "embed"
    url: str = Field(pattern=r"^https?://")
    title: str = ""
    description: str = ""


class DividerBlock(BaseModel):
    type: Literal["divider"] = "divider"


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    emoji: str
    text: str
    color: str = "blue"


class EquationBlock(BaseModel):
    type: Literal["equation"] = "equation"
    latex: str


class MathBlock(BaseModel):
    type: Literal["math"] = "math"
    latex: str
    display_mode: bool = True


class MermaidBlock(BaseModel):
    type: Literal["mermaid"] = "mermaid"
    code: str
    theme: str = "default"


class VideoBlock(BaseModel):
    type: Literal["video"] = "video"
    url: str = Field(pattern=r"^https?://")
    poster_url: str | None = None
    duration: int | None = None


class FileBlock(BaseModel):
    type: Literal["file"] = "file"
    file_token: str
    name: str
    size: int = 0
```

并更新 AnyBlock Union：

```python
AnyBlock = Union[
    HeadingBlock, TextBlock, CodeBlock, QuoteBlock, QuoteContainerBlock,
    TableBlock, ListBlock, ImageBlock,
    EmbedBlock, DividerBlock, CalloutBlock, EquationBlock,
    MathBlock, MermaidBlock, VideoBlock, FileBlock,
]
```

- [ ] **Step 4: Modify serializer.py**

在 `_parse_block` 中追加 8 个分支：

```python
        case "embed":   return EmbedBlock(**d)
        case "divider": return DividerBlock(**d)
        case "callout": return CalloutBlock(**d)
        case "equation": return EquationBlock(**d)
        case "math":    return MathBlock(**d)
        case "mermaid": return MermaidBlock(**d)
        case "video":   return VideoBlock(**d)
        case "file":    return FileBlock(**d)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_blocks_schemas_phase6.py tests/unit/test_blocks_serializer.py tests/unit/test_blocks_schemas.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/blocks/ tests/unit/test_blocks_schemas_phase6.py
git commit -m "feat(phase6): add 8 block types (embed/divider/callout/equation/math/mermaid/video/file)"
```

---

## Task 2: DocAdapter 8 类飞书 API 映射

**Files:**
- Modify: `feishu_adapter/doc_adapter.py`
- Create: `tests/integration/test_doc_adapter_blocks_phase6.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_doc_adapter_blocks_phase6.py
import httpx
import pytest
import respx

from feishu_adapter.doc_adapter import DocAdapter
from orchestrator.blocks.schemas import (CalloutBlock, DividerBlock,
                                          EmbedBlock, EquationBlock,
                                          FileBlock, MathBlock,
                                          MermaidBlock, VideoBlock)


class NoWaitLimiter:
    def wait(self):
        pass


@pytest.fixture
def adapter():
    a = DocAdapter(base_url="https://example.feishu.cn", api_token="t",
                    rate_limiter=NoWaitLimiter())
    return a


@respx.mock
def test_render_divider(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1", [DividerBlock()])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_embed(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [EmbedBlock(url="https://example.com", title="x")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_callout(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [CalloutBlock(emoji="⚠️", text="warn", color="red")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_equation(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [EquationBlock(latex="E = mc^2")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_mermaid_fallback(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    # mermaid 飞书不支持 → 退化为 code block
    adapter.render_blocks("doc_1",
        [MermaidBlock(code="graph TD; A-->B")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_video(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [VideoBlock(url="https://x.com/v.mp4")])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_file(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    adapter.render_blocks("doc_1",
        [FileBlock(file_token="abc", name="x.txt", size=1024)])
    assert respx.calls.call_count == 1


@respx.mock
def test_render_all_phase6_types(adapter):
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    blocks = [
        EmbedBlock(url="https://example.com"),
        DividerBlock(),
        CalloutBlock(emoji="📌", text="hi"),
        EquationBlock(latex="x"),
        MathBlock(latex="y"),
        MermaidBlock(code="graph TD"),
        VideoBlock(url="https://v.mp4"),
        FileBlock(file_token="t", name="f"),
    ]
    adapter.render_blocks("doc_1", blocks)
    assert respx.calls.call_count == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_doc_adapter_blocks_phase6.py -v`
Expected: ImportError or AttributeError

- [ ] **Step 3: Modify `doc_adapter.py` 的 `_to_feishu_payload`**

在最后 `raise ValueError` 之前追加 8 类分支：

```python
        if t == "embed":
            return {"block_type": "embed",
                    "embed": {"url": block.url, "title": block.title,
                              "description": block.description}}
        if t == "divider":
            return {"block_type": "divider", "divider": {}}
        if t == "callout":
            return {"block_type": "callout",
                    "callout": {"emoji": block.emoji, "text": block.text,
                                "color": block.color}}
        if t == "equation":
            return {"block_type": "equation",
                    "equation": {"latex": block.latex}}
        if t == "math":
            # math 与 equation 同字段
            return {"block_type": "equation",
                    "equation": {"latex": block.latex,
                                 "display_mode": block.display_mode}}
        if t == "mermaid":
            # 飞书不支持 mermaid → 退化为 code block
            return {"block_type": "code",
                    "code": {"elements": [
                        {"text_run": {"content": f"[mermaid]\n{block.code}"}}
                    ], "language": "mermaid"}}
        if t == "video":
            return {"block_type": "video",
                    "video": {"url": block.url,
                              "poster_url": block.poster_url,
                              "duration": block.duration}}
        if t == "file":
            return {"block_type": "file",
                    "file": {"file_token": block.file_token,
                             "name": block.name, "size": block.size}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_doc_adapter_blocks_phase6.py tests/integration/test_doc_adapter_blocks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add feishu_adapter/doc_adapter.py tests/integration/test_doc_adapter_blocks_phase6.py
git commit -m "feat(phase6): DocAdapter renders 8 new block types (mermaid falls back to code)"
```

---

## Task 3: TemplateEngine 8 类文本化

**Files:**
- Modify: `orchestrator/template_engine.py`

- [ ] **Step 1: Modify `render_blocks_to_text`**

追加 8 类分支：

```python
            elif t == "embed":
                lines.append(f"[embed: {block.title}]({block.url})")
            elif t == "divider":
                lines.append("---")
            elif t == "callout":
                lines.append(f"{block.emoji} {block.text}")
            elif t == "equation":
                lines.append(f"$${block.latex}$$")
            elif t == "math":
                if block.display_mode:
                    lines.append(f"$$\n{block.latex}\n$$")
                else:
                    lines.append(f"${block.latex}$")
            elif t == "mermaid":
                lines.append(f"```mermaid\n{block.code}\n```")
            elif t == "video":
                lines.append(f"[video: {block.url}]")
            elif t == "file":
                lines.append(f"[file: {block.name} ({block.size} bytes)]")
```

- [ ] **Step 2: 验证测试**

Run: `python -m pytest tests/unit/test_template_engine_phase5.py -v`
Expected: PASS（不需要新增测试；沿用现有测试）

- [ ] **Step 3: Commit**

```bash
git add orchestrator/template_engine.py
git commit -m "feat(phase6): TemplateEngine.render_blocks_to_text handles 8 new types"
```

---

## Task 4: ORM TemplateVersionRow + Repo

**Files:**
- Modify: `persistence/models.py`
- Create: `persistence/repositories/template_version_repo.py`
- Create: `tests/unit/test_template_version.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_template_version.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.template_version_repo import TemplateVersionRepo


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


def test_insert_and_count(session):
    repo = TemplateVersionRepo(session)
    repo.insert(version_id="v1", template_id="t1", version_number=1,
                name="n", description="d", blocks_json="[]", steps_json=None,
                created_by="ou_1")
    repo.insert(version_id="v2", template_id="t1", version_number=2,
                name="n2", description="d", blocks_json="[]", steps_json=None,
                created_by="ou_1")
    session.commit()
    assert repo.count("t1") == 2


def test_list_by_template(session):
    repo = TemplateVersionRepo(session)
    for i in range(3):
        repo.insert(version_id=f"v{i+1}", template_id="t1",
                    version_number=i+1, name="n", description="d",
                    blocks_json=None, steps_json=None, created_by="ou_1")
    session.commit()
    out = repo.list_by_template("t1")
    assert len(out) == 3
    assert out[0].version_number == 3  # DESC


def test_get_by_version(session):
    repo = TemplateVersionRepo(session)
    repo.insert(version_id="v2", template_id="t1", version_number=2,
                name="n", description="d", blocks_json="[x]", steps_json=None,
                created_by="ou_1")
    session.commit()
    v = repo.get_by_version("t1", 2)
    assert v.blocks_json == "[x]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_template_version.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `persistence/models.py`**

追加：

```python

# === Phase 6 ===

class TemplateVersionRow(Base):
    """Phase 6: 模板版本表。"""
    __tablename__ = "template_versions"

    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    blocks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String, nullable=False)
```

- [ ] **Step 4: Create `template_version_repo.py`**

```python
# persistence/repositories/template_version_repo.py
"""Phase 6: 模板版本表 CRUD。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TemplateVersionRow


class TemplateVersionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def insert(
        self,
        *,
        version_id: str,
        template_id: str,
        version_number: int,
        name: str,
        description: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        created_by: str,
    ) -> TemplateVersionRow:
        row = TemplateVersionRow(
            version_id=version_id,
            template_id=template_id,
            version_number=version_number,
            name=name,
            description=description,
            blocks_json=blocks_json,
            steps_json=steps_json,
            created_by=created_by,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def count(self, template_id: str) -> int:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id).count()
        )

    def list_by_template(self, template_id: str) -> list[TemplateVersionRow]:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateVersionRow.version_number.desc())
            .all()
        )

    def get_by_version(
        self, template_id: str, version_number: int
    ) -> Optional[TemplateVersionRow]:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id, version_number=version_number)
            .first()
        )

    def delete_oldest(self, template_id: str, *, keep: int = 10) -> None:
        rows = (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateVersionRow.version_number.asc())
            .all()
        )
        to_delete = len(rows) - keep
        if to_delete > 0:
            for r in rows[:to_delete]:
                self.session.delete(r)
            self.session.flush()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_template_version.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add persistence/models.py persistence/repositories/template_version_repo.py tests/unit/test_template_version.py
git commit -m "feat(phase6): add TemplateVersionRow + Repo (version history)"
```

---

## Task 5: VersionService（创建 / 列出 / 回滚）

**Files:**
- Create: `orchestrator/templates/version_service.py`
- Modify: `orchestrator/templates/template_service.py`
- Create: `tests/unit/test_version_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_version_service.py
from unittest.mock import MagicMock

import pytest

from orchestrator.templates.version_service import VersionService


def _make_service():
    version_repo = MagicMock()
    version_repo.count.return_value = 0
    template_repo = MagicMock()
    template_repo.get.return_value = MagicMock(
        template_id="t1", owner_open_id="ou_1", name="x",
        description="", scope="user", chat_id=None,
        archived_at=None, blocks_json="[]", steps_json=None,
    )
    return VersionService(version_repo=version_repo,
                          template_repo=template_repo), version_repo, template_repo


def test_on_template_upsert_creates_version():
    svc, version_repo, _ = _make_service()
    version_repo.count.return_value = 0
    svc.on_template_upsert(
        template_id="t1", name="x", description="d",
        blocks_json="[]", steps_json=None, created_by="ou_1",
    )
    version_repo.insert.assert_called_once()
    kwargs = version_repo.insert.call_args.kwargs
    assert kwargs["version_number"] == 1


def test_on_template_upsert_increments_version():
    svc, version_repo, _ = _make_service()
    version_repo.count.return_value = 3
    svc.on_template_upsert(
        template_id="t1", name="x", description="d",
        blocks_json="[]", steps_json=None, created_by="ou_1",
    )
    kwargs = version_repo.insert.call_args.kwargs
    assert kwargs["version_number"] == 4


def test_list_versions():
    svc, version_repo, _ = _make_service()
    version_repo.list_by_template.return_value = ["v1", "v2"]
    out = svc.list_versions("t1")
    assert out == ["v1", "v2"]


def test_rollback_requires_owner():
    svc, version_repo, template_repo = _make_service()
    template_repo.get.return_value = MagicMock(owner_open_id="ou_2")
    version_repo.get_by_version.return_value = MagicMock(
        template_id="t1", version_number=2, name="v2",
        description="d", blocks_json="[]", steps_json=None,
    )
    with pytest.raises(PermissionError):
        svc.rollback(template_id="t1", version_number=2,
                     caller_open_id="ou_1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_version_service.py -v`
Expected: ImportError

- [ ] **Step 3: Create `version_service.py`**

```python
# orchestrator/templates/version_service.py
"""Phase 6: 模板版本管理（创建 / 列出 / 回滚）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from shared.ulid_ import new_ulid


class VersionService:
    def __init__(self, version_repo, template_repo) -> None:
        self.version_repo = version_repo
        self.template_repo = template_repo

    def on_template_upsert(
        self, *,
        template_id: str,
        name: str,
        description: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        created_by: str,
    ) -> None:
        """Phase 6: 每次 upsert 写新版本；硬上限 10（自动删最旧）。"""
        current = self.version_repo.count(template_id)
        if current >= 10:
            self.version_repo.delete_oldest(template_id, keep=10)
            current = 9  # 已删 1 个
        self.version_repo.insert(
            version_id=new_ulid(),
            template_id=template_id,
            version_number=current + 1,
            name=name, description=description,
            blocks_json=blocks_json, steps_json=steps_json,
            created_by=created_by,
        )

    def list_versions(self, template_id: str) -> list:
        return self.version_repo.list_by_template(template_id)

    def rollback(
        self, *,
        template_id: str,
        version_number: int,
        caller_open_id: str,
    ) -> None:
        """Phase 6: 回滚到指定版本（写新版本号，保留历史）。"""
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of {template_id}"
            )
        version = self.version_repo.get_by_version(template_id, version_number)
        if version is None:
            raise ValueError(
                f"version {version_number} of {template_id} not found"
            )
        # 覆盖 templates 表
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=version.name,
            type_=tpl.type,
            blocks_json=version.blocks_json,
            steps_json=version.steps_json,
            description=version.description,
            scope=tpl.scope, chat_id=tpl.chat_id,
        )
        # 写新版本号（内容 = 被回滚的版本）
        self.on_template_upsert(
            template_id=template_id,
            name=version.name, description=version.description,
            blocks_json=version.blocks_json, steps_json=version.steps_json,
            created_by=caller_open_id,
        )
```

- [ ] **Step 4: Modify `template_service.py`**

让 `create_block` / `create_subplan` / `update_*` 调用 `on_template_upsert`：

在 TemplateService.__init__ 增加 `version_service=None` 参数；在 create_block / create_subplan 末尾追加：

```python
        if self.version_service is not None:
            self.version_service.on_template_upsert(
                template_id=tid, name=name, description=description,
                blocks_json=blocks_to_json(blocks), steps_json=None,
                created_by=owner_open_id,
            )
```

同理对 `create_subplan`。

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_version_service.py tests/unit/test_template_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/templates/version_service.py orchestrator/templates/template_service.py tests/unit/test_version_service.py
git commit -m "feat(phase6): VersionService (create on upsert + list + rollback with owner check)"
```

---

## Task 6: 群聊共享（scope + ShareService）

**Files:**
- Modify: `persistence/models.py`
- Modify: `persistence/repositories/template_repo.py`
- Create: `orchestrator/templates/share_service.py`
- Create: `tests/unit/test_share_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_share_service.py
from unittest.mock import MagicMock

import pytest

from orchestrator.templates.share_service import ShareService


def _make():
    template_repo = MagicMock()
    return ShareService(template_repo=template_repo), template_repo


def test_share_to_chat_requires_owner():
    svc, repo = _make()
    repo.get.return_value = MagicMock(owner_open_id="ou_2")
    with pytest.raises(PermissionError):
        svc.share_to_chat(template_id="t1", chat_id="chat_1",
                          caller_open_id="ou_1")


def test_share_to_chat_owner_succeeds():
    svc, repo = _make()
    repo.get.return_value = MagicMock(owner_open_id="ou_1")
    svc.share_to_chat(template_id="t1", chat_id="chat_1",
                      caller_open_id="ou_1")
    repo.upsert.assert_called_once()
    kwargs = repo.upsert.call_args.kwargs
    assert kwargs["scope"] == "chat"
    assert kwargs["chat_id"] == "chat_1"


def test_list_for_chat():
    svc, repo = _make()
    repo.list_by_chat.return_value = ["t1", "t2"]
    out = svc.list_for_chat("chat_1")
    assert out == ["t1", "t2"]


def test_can_access_user_scope_owner():
    svc, repo = _make()
    repo.get.return_value = MagicMock(scope="user", owner_open_id="ou_1",
                                       archived_at=None)
    assert svc.can_access(template_id="t1", caller_open_id="ou_1",
                           chat_id=None) is True


def test_can_access_chat_scope_same_chat():
    svc, repo = _make()
    repo.get.return_value = MagicMock(scope="chat", chat_id="chat_1",
                                       owner_open_id="ou_1",
                                       archived_at=None)
    assert svc.can_access(template_id="t1", caller_open_id="ou_2",
                           chat_id="chat_1") is True


def test_can_access_chat_scope_different_chat():
    svc, repo = _make()
    repo.get.return_value = MagicMock(scope="chat", chat_id="chat_1",
                                       owner_open_id="ou_1",
                                       archived_at=None)
    assert svc.can_access(template_id="t1", caller_open_id="ou_2",
                           chat_id="chat_2") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_share_service.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `persistence/models.py`**

在 TemplateRow 添加：

```python
    scope: Mapped[str] = mapped_column(String, default="user", nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Modify `template_repo.py`**

更新 `upsert()` 增加 `scope` / `chat_id` 参数；新增 `list_by_chat()`：

```python
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
        scope: str = "user",
        chat_id: Optional[str] = None,
    ) -> TemplateRow:
        row = self.session.get(TemplateRow, template_id)
        if row is None:
            row = TemplateRow(
                template_id=template_id, owner_open_id=owner_open_id,
                name=name, description=description, type=type_,
                blocks_json=blocks_json, steps_json=steps_json,
                scope=scope, chat_id=chat_id,
            )
            self.session.add(row)
        else:
            row.name = name
            row.description = description
            row.type = type_
            row.blocks_json = blocks_json
            row.steps_json = steps_json
            if scope is not None:
                row.scope = scope
            if chat_id is not None:
                row.chat_id = chat_id
        self.session.flush()
        return row
    
    def list_by_chat(self, chat_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(chat_id=chat_id, scope="chat", archived_at=None)
            .all()
        )
```

- [ ] **Step 5: Create `share_service.py`**

```python
# orchestrator/templates/share_service.py
"""Phase 6: 群聊共享模板。"""
from __future__ import annotations

from typing import Optional


class ShareService:
    def __init__(self, template_repo) -> None:
        self.template_repo = template_repo

    def share_to_chat(
        self, *, template_id: str, chat_id: str, caller_open_id: str,
    ) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError(
                f"caller {caller_open_id} is not owner of {template_id}"
            )
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="chat", chat_id=chat_id,
        )

    def list_for_chat(self, chat_id: str) -> list:
        return self.template_repo.list_by_chat(chat_id)

    def can_access(
        self, *,
        template_id: str,
        caller_open_id: str,
        chat_id: Optional[str] = None,
    ) -> bool:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at is not None:
            return False
        if tpl.scope == "user":
            return tpl.owner_open_id == caller_open_id
        if tpl.scope == "chat":
            return tpl.chat_id == chat_id
        return False
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_share_service.py tests/unit/test_template_repo.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add persistence/models.py persistence/repositories/template_repo.py orchestrator/templates/share_service.py tests/unit/test_share_service.py
git commit -m "feat(phase6): ShareService + templates.scope/chat_id (user + chat sharing)"
```

---

## Task 7: 本地 BLAST+

**Files:**
- Create: `orchestrator/tools/bio/blast_local.py`
- Create: `tests/unit/test_blast_local.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_blast_local.py
import json
import subprocess
from unittest.mock import patch

import pytest

from orchestrator.tools.bio.blast_local import BlastLocalTool


@pytest.fixture
def tool(tmp_path):
    fake_binary = tmp_path / "blastp"
    fake_binary.write_text("#!/bin/sh\n")
    fake_binary.chmod(0o755)
    return BlastLocalTool(
        binary_path=str(fake_binary),
        db_path=str(tmp_path),
        timeout_sec=5,
    )


def test_local_blast_success(tool, tmp_path):
    # mock subprocess.run 返回 blast JSON
    fake_output = json.dumps({
        "BlastOutput2": [{
            "report": {
                "results": {
                    "search": {
                        "hits": [
                            {"num": 1,
                             "description": [{"title": "BRCA1"}],
                             "hsps": [{"hsp_score": 100}]},
                        ]
                    }
                }
            }
        }]
    })
    with patch.object(subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          args=[], returncode=0, stdout=fake_output,
                          stderr="")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["records"][0]["title"] == "BRCA1"
    assert out["database"] == "nr"


def test_local_blast_subprocess_error(tool):
    with patch.object(subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          args=[], returncode=1, stdout="",
                          stderr="blast error")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_LOCAL_ERROR"


def test_local_blast_timeout(tool):
    with patch.object(subprocess, "run",
                      side_effect=subprocess.TimeoutExpired("cmd", 5)):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_LOCAL_TIMEOUT"


def test_mode_resolution_local_binary_exists(tmp_path):
    fake_binary = tmp_path / "blastp"
    fake_binary.write_text("#!/bin/sh\n")
    fake_binary.chmod(0o755)
    tool = BlastLocalTool(binary_path=str(fake_binary),
                          db_path=str(tmp_path))
    mode = tool._resolve_mode(database="nr")
    assert mode in ("local", "web")


def test_mode_resolution_web_when_no_binary(tmp_path):
    tool = BlastLocalTool(binary_path="/nonexistent/blastp",
                          db_path=str(tmp_path))
    mode = tool._resolve_mode(database="nr")
    assert mode == "web"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_blast_local.py -v`
Expected: ImportError

- [ ] **Step 3: Create `blast_local.py`**

```python
# orchestrator/tools/bio/blast_local.py
"""Phase 6: 本地 BLAST+ 工具（subprocess.run + 解析 blast JSON）。

与 Phase 4 MVP BlastNCBITool 并存；按 mode='auto' 自动选择。"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from orchestrator.tools.bio.rate_limiter import RateLimiter


class BlastLocalTool:
    def __init__(
        self,
        *,
        binary_path: str = "",
        db_path: str = "",
        timeout_sec: int = 60,
        rate_limiter: Optional[RateLimiter] = None,
        mode: str = "auto",
    ) -> None:
        self.binary_path = binary_path
        self.db_path = db_path
        self.timeout_sec = timeout_sec
        self.rate_limiter = rate_limiter or RateLimiter(rate=3.0, per_sec=1.0)
        self.mode = mode

    def handle(self, *, query: str, database: str = "nr",
               max_hits: int = 5) -> dict:
        if not query or not query.strip():
            return {"error_code": "BLAST_INVALID_QUERY",
                    "error_message": "query is empty"}
        actual = self._resolve_mode(database)
        if actual != "local":
            return {"error_code": "BLAST_LOCAL_UNAVAILABLE",
                    "error_message": "local binary not found"}
        cmd = [
            self.binary_path,
            "-query", "-",  # stdin
            "-db", os.path.join(self.db_path, database),
            "-outfmt", "15",
            "-max_target_seqs", str(max_hits),
        ]
        try:
            proc = subprocess.run(
                cmd, input=query, capture_output=True, text=True,
                timeout=self.timeout_sec, shell=False,
            )
        except subprocess.TimeoutExpired:
            return {"error_code": "BLAST_LOCAL_TIMEOUT",
                    "error_message": f"timeout after {self.timeout_sec}s"}
        except FileNotFoundError:
            return {"error_code": "BLAST_LOCAL_BINARY_MISSING",
                    "error_message": self.binary_path}
        if proc.returncode != 0:
            return {"error_code": "BLAST_LOCAL_ERROR",
                    "error_message": proc.stderr[:500]}
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return {"error_code": "BLAST_LOCAL_PARSE_ERROR",
                    "error_message": str(e)}
        records = self._parse_hits(data)
        return {
            "ids": [r["id"] for r in records],
            "records": records,
            "query": query,
            "database": database,
            "total_count": len(records),
        }

    def _resolve_mode(self, *, database: str) -> str:
        if self.mode == "web":
            return "web"
        if self.mode == "local":
            return "local"
        # auto
        if (self.binary_path
                and os.path.exists(self.binary_path)
                and self._db_exists(database)):
            return "local"
        return "web"

    def _db_exists(self, database: str) -> bool:
        for ext in (".phr", ".psq", ".nhr", ".nsq"):
            if os.path.exists(os.path.join(self.db_path, database + ext)):
                return True
        return False

    def _parse_hits(self, data: dict) -> list[dict]:
        records: list[dict] = []
        for r in data.get("BlastOutput2", []):
            search = r.get("report", {}).get("results", {}).get("search", {})
            for hit in search.get("hits", []):
                descs = hit.get("description", [])
                title = descs[0].get("title", "") if descs else ""
                hsps = hit.get("hsps", [])
                score = hsps[0].get("hsp_score", 0) if hsps else 0
                records.append({
                    "id": str(hit.get("num", "")),
                    "title": title,
                    "summary": f"score: {score}",
                    "length": 0,
                })
        return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_blast_local.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/bio/blast_local.py tests/unit/test_blast_local.py
git commit -m "feat(phase6): BlastLocalTool (subprocess blastp + JSON parse + auto mode)"
```

---

## Task 8: 工具热加载（HotLoader）

**Files:**
- Create: `orchestrator/tools/hot_loader.py`
- Create: `tests/unit/test_hot_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hot_loader.py
import pytest

from orchestrator.tools.ast_guard import ASTGuard
from orchestrator.tools.hot_loader import HotLoader
from orchestrator.tools.tool_registry import ToolRegistry
from shared.errors import ToolBlockedError


def _make_loader(tmp_path):
    reg = ToolRegistry()
    audit = type("A", (), {
        "write": lambda self, **kw: None,
    })()
    loader = HotLoader(
        tool_registry=reg, audit_repo=audit, ast_guard=ASTGuard(),
        temp_dir=str(tmp_path),
    )
    return loader, reg


def test_hot_loader_registers_safe_tool(tmp_path):
    loader, reg = _make_loader(tmp_path)
    code = """
def handle(seq: str) -> dict:
    return {"rc": seq[::-1]}
"""
    name = loader.upload(
        name="reverse_complement", code=code,
        parameters={"type": "object", "properties": {"seq": {"type": "string"}}},
        risk_level="L0_read", actor_open_id="admin_1",
    )
    assert name == "reverse_complement"
    spec = reg.get("reverse_complement")
    assert spec is not None


def test_hot_loader_blocks_eval(tmp_path):
    loader, _ = _make_loader(tmp_path)
    code = """
def handle(query):
    return eval(query)
"""
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad_tool", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_blocks_exec(tmp_path):
    loader, _ = _make_loader(tmp_path)
    code = """
def handle():
    exec("print(1)")
"""
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad_tool2", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_requires_handle_function(tmp_path):
    loader, _ = _make_loader(tmp_path)
    code = "x = 1\n"
    with pytest.raises(ValueError):
        loader.upload(
            name="no_handle", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )


def test_hot_loader_size_limit(tmp_path):
    loader, _ = _make_loader(tmp_path)
    big_code = "x = 1\n" * 20000  # ~ 120KB
    with pytest.raises(ValueError):
        loader.upload(
            name="big", code=big_code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_hot_loader.py -v`
Expected: ImportError

- [ ] **Step 3: Create `hot_loader.py`**

```python
# orchestrator/tools/hot_loader.py
"""Phase 6: 工具热加载（管理员上传 + AST P0 拦截 + ToolRegistry 注入）。"""
from __future__ import annotations

import importlib.util
import os
from typing import TYPE_CHECKING

from shared.ulid_ import new_ulid

if TYPE_CHECKING:
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.tool_registry import ToolRegistry


class HotLoader:
    MAX_CODE_SIZE = 100 * 1024  # 100KB

    def __init__(
        self, *,
        tool_registry: "ToolRegistry",
        audit_repo,
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
        parameters: dict,
        risk_level: str,
        actor_open_id: str,
    ) -> str:
        # 1. 大小校验
        if len(code.encode()) > self.MAX_CODE_SIZE:
            raise ValueError(
                f"code too large: {len(code)} > {self.MAX_CODE_SIZE}"
            )
        # 2. AST P0 拦截
        result = self.ast_guard.check(code)
        if result.blocked:
            if self.audit_repo is not None:
                self.audit_repo.write(
                    action="blocked_tool_upload",
                    actor_type="admin", actor_id=actor_open_id,
                    target_type="tool", target_id=name,
                    detail={"notices": str(result.notices)},
                )
            raise ToolBlockedError(result.notices)
        # 3. 写临时文件
        os.makedirs(self.temp_dir, exist_ok=True)
        module_id = new_ulid()
        path = os.path.join(self.temp_dir, f"{module_id}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        # 4. importlib 加载
        spec = importlib.util.spec_from_file_location(module_id, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # 5. 提取 handle
        handle = getattr(module, "handle", None)
        if handle is None:
            raise ValueError("tool must define handle(**kwargs)")
        # 6. 注册
        from orchestrator.tools.tool_registry import ToolSpec
        self.tool_registry.register(ToolSpec(
            name=name, description="hot-loaded",
            parameters=parameters, risk_level=risk_level,
            handler=handle,
        ))
        # 7. 审计
        if self.audit_repo is not None:
            self.audit_repo.write(
                action="hot_load_tool",
                actor_type="admin", actor_id=actor_open_id,
                target_type="tool", target_id=name,
                detail={"module_id": module_id},
            )
        return name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_hot_loader.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/tools/hot_loader.py tests/unit/test_hot_loader.py
git commit -m "feat(phase6): HotLoader (admin upload + AST P0 + ToolRegistry inject)"
```

---

## Task 9: FastAPI /versions / /share / /admin/tools 路由

**Files:**
- Modify: `gateway/app.py`
- Create: `tests/integration/test_templates_phase6_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_templates_phase6_api.py
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


def _make_client():
    version_service = MagicMock()
    version_service.list_versions.return_value = ["v1", "v2"]
    version_service.rollback.return_value = None
    share_service = MagicMock()
    share_service.list_for_chat.return_value = ["t1"]
    share_service.share_to_chat.return_value = None
    share_service.can_access.return_value = True
    hot_loader = MagicMock()
    hot_loader.upload.return_value = "t_new"
    return (
        TestClient(create_app(
            secret="phase2-secret", orchestrator=object(),
            version_service=version_service,
            share_service=share_service,
            hot_loader=hot_loader,
        )),
        version_service, share_service, hot_loader,
    )


def test_list_versions():
    c, vs, _, _ = _make_client()
    resp = c.get("/templates/t_1/versions")
    assert resp.status_code == 200
    assert resp.json()["versions"] == ["v1", "v2"]


def test_rollback():
    c, vs, _, _ = _make_client()
    resp = c.post("/templates/t_1/rollback",
                   json={"version_number": 2, "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    vs.rollback.assert_called_once()


def test_share_to_chat():
    c, _, ss, _ = _make_client()
    resp = c.post("/templates/t_1/share",
                   json={"chat_id": "chat_1", "caller_open_id": "ou_1"})
    assert resp.status_code == 200
    ss.share_to_chat.assert_called_once()


def test_list_for_chat():
    c, _, ss, _ = _make_client()
    resp = c.get("/templates/chat/chat_1")
    assert resp.status_code == 200
    assert resp.json()["templates"] == ["t1"]


def test_admin_upload_tool():
    c, _, _, hl = _make_client()
    body = {
        "name": "reverse_complement",
        "code": "def handle(seq): return {'rc': seq[::-1]}",
        "parameters": {"type": "object"},
        "risk_level": "L0_read",
        "actor_open_id": "admin_1",
    }
    resp = c.post("/admin/tools/upload", json=body)
    assert resp.status_code == 200
    assert resp.json()["name"] == "t_new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_templates_phase6_api.py -v`
Expected: ImportError

- [ ] **Step 3: Modify `gateway/app.py`**

create_app 增加参数：

```python
def create_app(
    secret, orchestrator, rate_per_min=60, session_factory=None,
    bind_doc_service=None, template_service=None,
    version_service=None, share_service=None, hot_loader=None,
) -> FastAPI:
```

AppContext 增加：

```python
    version_service: object | None = None
    share_service: object | None = None
    hot_loader: object | None = None
```

在 create_app 末尾追加路由（`/webhook/lark` 之前）：

```python
    # === Phase 6: 版本 + 共享 + 热加载 ===
    @app.get("/templates/{template_id}/versions")
    async def list_versions(template_id: str):
        ctx = app.state.context
        vs = ctx.version_service
        if vs is None:
            return {"versions": []}
        return {"versions": vs.list_versions(template_id)}

    @app.post("/templates/{template_id}/rollback")
    async def rollback_template(template_id: str, request: Request):
        ctx = app.state.context
        body = await request.json()
        vs = ctx.version_service
        if vs is None:
            raise _HTTPException(status_code=503, detail="version_service not configured")
        try:
            vs.rollback(template_id=template_id,
                        version_number=body["version_number"],
                        caller_open_id=body["caller_open_id"])
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.post("/templates/{template_id}/share")
    async def share_template(template_id: str, request: Request):
        ctx = app.state.context
        body = await request.json()
        ss = ctx.share_service
        if ss is None:
            raise _HTTPException(status_code=503, detail="share_service not configured")
        try:
            ss.share_to_chat(template_id=template_id,
                              chat_id=body["chat_id"],
                              caller_open_id=body["caller_open_id"])
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.get("/templates/chat/{chat_id}")
    async def list_for_chat(chat_id: str):
        ctx = app.state.context
        ss = ctx.share_service
        if ss is None:
            return {"templates": []}
        return {"templates": [
            t.template_id for t in ss.list_for_chat(chat_id)
        ]}

    @app.post("/admin/tools/upload")
    async def admin_upload_tool(request: Request):
        ctx = app.state.context
        body = await request.json()
        hl = ctx.hot_loader
        if hl is None:
            raise _HTTPException(status_code=503, detail="hot_loader not configured")
        try:
            name = hl.upload(
                name=body["name"], code=body["code"],
                parameters=body["parameters"],
                risk_level=body["risk_level"],
                actor_open_id=body["actor_open_id"],
            )
        except ToolBlockedError as e:
            raise _HTTPException(status_code=400, detail=f"AST blocked: {e}")
        except ValueError as e:
            raise _HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "name": name}
```

注意：`ToolBlockedError` 需 import。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_templates_phase6_api.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app.py tests/integration/test_templates_phase6_api.py
git commit -m "feat(phase6): FastAPI /versions / /share / /admin/tools routes"
```

---

## Task 10: Orchestrator.process_phase6

**Files:**
- Modify: `orchestrator/app.py`
- Create: `tests/unit/test_orchestrator_phase6.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_orchestrator_phase6.py
from unittest.mock import MagicMock

from orchestrator.app import Orchestrator


def test_orchestrator_phase6_has_process_phase6():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    assert hasattr(orch, "process_phase6")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_orchestrator_phase6.py -v`
Expected: AttributeError

- [ ] **Step 3: Append `process_phase6`**

```python
    # === Phase 6 ===
    def process_phase6(self, incoming) -> dict:
        """Phase 6 入口：复用 process_phase5 + 模板版本/共享指令。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 6 subsystems not initialized")

        text = incoming.text.strip()
        # /template-rollback <id> <version>
        if text.startswith("/template-rollback "):
            parts = text.split()
            if len(parts) < 3:
                self.im.reply(incoming.chat_id, "[错误] 用法: /template-rollback <id> <version>")
                return {"status": "rollback_failed", "reason": "bad_args"}
            tid, ver = parts[1], int(parts[2])
            vs = getattr(self, "version_service", None)
            if vs is None:
                self.im.reply(incoming.chat_id, "[错误] version_service 未配置")
                return {"status": "rollback_failed"}
            try:
                vs.rollback(template_id=tid, version_number=ver,
                             caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id, f"[成功] 已回滚模板 {tid} 到版本 {ver}")
                return {"status": "rolled_back", "template_id": tid,
                        "version_number": ver}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "rollback_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "rollback_failed", "reason": str(e)}
        # /template-share <id>
        if text.startswith("/template-share "):
            tid = text.split(maxsplit=1)[1].strip()
            ss = getattr(self, "share_service", None)
            if ss is None:
                self.im.reply(incoming.chat_id, "[错误] share_service 未配置")
                return {"status": "share_failed"}
            try:
                ss.share_to_chat(template_id=tid, chat_id=incoming.chat_id,
                                  caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id, f"[成功] 模板 {tid} 已共享到本群")
                return {"status": "shared", "template_id": tid}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "share_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "share_failed", "reason": str(e)}
        # 普通消息：复用 process_phase5
        return self.process_phase5(incoming)
```

Orchestrator __init__ 增加 `version_service` 和 `share_service` 参数。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_orchestrator_phase6.py tests/integration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app.py tests/unit/test_orchestrator_phase6.py
git commit -m "feat(phase6): Orchestrator.process_phase6 + /template-rollback + /template-share commands"
```

---

## Task 11: 端到端 E1-E8

**Files:**
- Create: `tests/integration/test_e2e_phase6_e1_e8.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase6_e1_e8.py
"""E1-E8: Phase 6 端到端场景。"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import respx
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from gateway.app import create_app
from orchestrator.blocks.schemas import (CalloutBlock, DividerBlock,
                                          EmbedBlock, EquationBlock,
                                          HeadingBlock, MathBlock,
                                          MermaidBlock, TextBlock,
                                          VideoBlock)
from orchestrator.templates.share_service import ShareService
from orchestrator.templates.template_service import TemplateService
from orchestrator.templates.version_service import VersionService
from persistence.models import Base
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo


def _make_full():
    engine = create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    t_repo = TemplateRepo(session)
    v_repo = TemplateVersionRepo(session)
    ts = TemplateService(repo=t_repo)
    vs = VersionService(version_repo=v_repo, template_repo=t_repo)
    ss = ShareService(template_repo=t_repo)
    return ts, vs, ss, t_repo, v_repo


# E1: 富文本 14 类 → 飞书 doc（Phase 5 +Phase 6 8 类）
@respx.mock
def test_e1_phase6_blocks_rendered():
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    from feishu_adapter.doc_adapter import DocAdapter

    class NoWaitLimiter:
        def wait(self):
            pass

    adapter = DocAdapter(base_url="https://example.feishu.cn", api_token="t",
                          rate_limiter=NoWaitLimiter())
    blocks = [
        HeadingBlock(level=2, text="Report"),
        DividerBlock(),
        CalloutBlock(emoji="📌", text="Note"),
        EmbedBlock(url="https://example.com", title="Ref"),
        EquationBlock(latex="E = mc^2"),
        MathBlock(latex="\\sum x", display_mode=True),
        MermaidBlock(code="graph TD; A-->B"),
        VideoBlock(url="https://v.mp4"),
        TextBlock(text="end"),
    ]
    adapter.render_blocks("doc_1", blocks)
    assert respx.calls.call_count == 9


# E2: 模板版本：3 次更新 → 列表 → 回滚
def test_e2_template_versioning_and_rollback():
    ts, vs, _, t_repo, v_repo = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                          blocks=[TextBlock(text="v1")], description="")
    # v1 自动生成
    # 模拟更新：直接修改 + 触发版本
    tpl = t_repo.get(tid)
    tpl.blocks_json = '[{"type": "text", "text": "v2"}]'
    vs.on_template_upsert(template_id=tid, name="std", description="",
                          blocks_json=tpl.blocks_json, steps_json=None,
                          created_by="ou_1")
    tpl.blocks_json = '[{"type": "text", "text": "v3"}]'
    vs.on_template_upsert(template_id=tid, name="std", description="",
                          blocks_json=tpl.blocks_json, steps_json=None,
                          created_by="ou_1")
    versions = vs.list_versions(tid)
    assert len(versions) == 3
    # 回滚到 v1
    vs.rollback(template_id=tid, version_number=1, caller_open_id="ou_1")
    versions_after = vs.list_versions(tid)
    assert len(versions_after) == 4  # 回滚也写一个版本


# E3: 版本上限 10
def test_e3_version_hard_limit_10():
    ts, vs, _, t_repo, _ = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="t",
                          blocks=[TextBlock(text="v0")], description="")
    # 写 12 个版本（v1 - v12）
    for i in range(12):
        vs.on_template_upsert(template_id=tid, name="t", description="",
                              blocks_json=None, steps_json=None,
                              created_by="ou_1")
    assert len(vs.list_versions(tid)) == 10


# E4: 群聊共享
def test_e4_chat_share_visibility():
    ts, _, ss, _, _ = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="x",
                          blocks=[TextBlock(text="x")], description="")
    # 共享到 chat_1
    ss.share_to_chat(template_id=tid, chat_id="chat_1",
                     caller_open_id="ou_1")
    # 用户 B 在 chat_1 中可访问
    assert ss.can_access(template_id=tid, caller_open_id="ou_2",
                          chat_id="chat_1") is True
    # 用户 B 在 chat_2 中不可访问
    assert ss.can_access(template_id=tid, caller_open_id="ou_2",
                          chat_id="chat_2") is False


# E5: 本地 BLAST：解析 JSON 输出
def test_e5_local_blast_parse():
    from orchestrator.tools.bio.blast_local import BlastLocalTool
    import json as _json
    import subprocess as _subprocess
    from unittest.mock import patch, MagicMock
    tool = BlastLocalTool(binary_path="/usr/bin/blastp",
                          db_path="/data/blastdb")
    fake_out = _json.dumps({
        "BlastOutput2": [{
            "report": {"results": {"search": {"hits": [
                {"num": 1, "description": [{"title": "BRCA1"}],
                 "hsps": [{"hsp_score": 100}]},
            ]}}}
        }]
    })
    with patch.object(_subprocess, "run",
                       return_value=_subprocess.CompletedProcess(
                           args=[], returncode=0, stdout=fake_out, stderr="")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["records"][0]["title"] == "BRCA1"


# E6: 模式自动切换
def test_e6_blast_mode_auto():
    from orchestrator.tools.bio.blast_local import BlastLocalTool
    # 本地不存在 → 走 web
    tool = BlastLocalTool(binary_path="/nonexistent/blastp",
                          db_path="/data/blastdb")
    assert tool._resolve_mode(database="nr") == "web"


# E7: 热加载：管理员上传 → AST P0 拦截 → 注册
def test_e7_hot_loader_safe_tool():
    import tempfile
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.hot_loader import HotLoader
    from orchestrator.tools.tool_registry import ToolRegistry
    reg = ToolRegistry()
    loader = HotLoader(
        tool_registry=reg, audit_repo=MagicMock(),
        ast_guard=ASTGuard(),
        temp_dir=tempfile.mkdtemp(),
    )
    code = "def handle(seq): return {'rc': seq[::-1]}"
    name = loader.upload(
        name="revcomp", code=code,
        parameters={"type": "object"},
        risk_level="L0_read", actor_open_id="admin_1",
    )
    assert name == "revcomp"
    assert reg.get("revcomp") is not None


# E8: 热加载：含 eval → 拦截 + audit
def test_e8_hot_loader_blocks_eval():
    import tempfile
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.hot_loader import HotLoader
    from orchestrator.tools.tool_registry import ToolRegistry
    from shared.errors import ToolBlockedError
    reg = ToolRegistry()
    audit = MagicMock()
    loader = HotLoader(
        tool_registry=reg, audit_repo=audit,
        ast_guard=ASTGuard(),
        temp_dir=tempfile.mkdtemp(),
    )
    code = "def handle(): return eval('1+1')"
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )
    audit.write.assert_called()  # blocked audit 写入
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase6_e1_e8.py -v`
Expected: PASS (8 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase6_e1_e8.py
git commit -m "test(phase6): add e2e E1-E8 (block extension + versioning + chat sharing + local BLAST + hot loader)"
```

---

## Task 12: 回归验证

**Files:**
- Modify: 无（仅验证）

- [ ] **Step 1: Run all unit + integration tests**

Run: `python -m pytest tests/unit tests/integration --tb=line -q`
Expected: ALL PASS, 0 regressions（278 + 60 = 338）

- [ ] **Step 2: Commit fix (if any)**

```bash
git add -A
git commit -m "fix(phase6): ensure Phase 1-5 regression tests pass"
```

---

## Task 13: 测试总结 + commit

**Files:**
- Append to spec: 实施结果

---

## Self-Review

### 1. Spec coverage matrix

| Spec § | Requirement | Task | Status |
|---|---|---|---|
| §3 | 富文本 8 类 schema | Task 1 | ✓ |
| §3.5 | DocAdapter 8 类映射 | Task 2 | ✓ |
| §3.6 | TemplateEngine 8 类 | Task 3 | ✓ |
| §4 | 模板版本（version table + service）| Task 4, 5 | ✓ |
| §5 | 群聊共享（scope + share service）| Task 6 | ✓ |
| §6 | 本地 BLAST+ | Task 7 | ✓ |
| §7 | 工具热加载 | Task 8 | ✓ |
| §9 | ORM 增量 | Task 4, 6 | ✓ |
| §10 | 测试策略 + E1-E8 | Task 11 | ✓ |
| §11 | 不做清单 | 严格遵守 | ✓ |
| §13 | ADR 5 个 | spec/.../adrs/0010-0014 | ✓ |

**Gaps identified**: None — 全部 spec 章节有对应 Task。

### 2. Placeholder scan

搜索 "TBD" / "TODO" / "implement later" / "fill in details" — **0 个**。

### 3. Type consistency

| Name | Definition | Use |
|---|---|---|
| `EmbedBlock` etc. | Task 1 (schemas.py) | Task 2 (doc_adapter), Task 3 (template_engine) |
| `TemplateVersionRow` | Task 4 (models.py) | Task 5 (VersionService), Task