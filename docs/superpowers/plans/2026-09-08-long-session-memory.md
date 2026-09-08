# 长会话记忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 私聊多轮记忆——全量历史注入 + ≥80% 压缩 + ≥95% 冻结开新会话 + /clear 手动重置，复活 Phase 3 的 compressor/freeze 组件。

**Architecture:** 方案甲服务层接入：MessageRepo（持久）→ ChatMemory（编排：装配/压缩回写/冻结续跑//clear）→ Orchestrator.process 闲聊路径显式接入。所有外部故障降级为无记忆，不阻断聊天。

**Tech Stack:** SQLAlchemy + alembic + pydantic（既有栈，无新依赖）。

**Spec:** `docs/superpowers/specs/2026-09-08-long-session-memory-design.md`
（两处与现状的偏差已修订：context_token_budget 等 4 个 settings 字段已存在复用；
MessageRepo 用 replace_all 取代 delete_many）

**项目纪律（每个 Task 都适用）：**
- 测试一律 `.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest_tmp ...`（本机 Temp ACL 损坏）
- SearchReplace 有假成功前科：编辑后用 Read/Select-String 验证落盘，失败用 PowerShell `[IO.File]` 直写兜底
- 函数级中文注释；提交前 ruff + mypy 双平台必须绿
- 提交信息避免含 `cmd /c` 字样（安全过滤会拦）

---

### Task 1: MessageRow + 迁移 0003 + MessageRepo

**Files:**
- Modify: `persistence/models.py`（文件末尾追加 MessageRow）
- Create: `migrations/versions/0003_messages.py`
- Create: `persistence/repositories/message_repo.py`
- Test: `tests/unit/test_message_repo.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_message_repo.py`：

```python
"""长会话记忆：MessageRepo 真库测试（sqlite StaticPool 既有模式）。"""
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base
from persistence.repositories.message_repo import MessageRepo


@pytest.fixture
def session():
    """sqlite 内存真库（StaticPool 共享连接，与既有真库测试同模式）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_messages_table_and_index_created(session):
    """建表含 messages 与 session_id 索引（迁移与模型对齐的护栏）。"""
    insp = inspect(session.get_bind())
    assert "messages" in insp.get_table_names()
    idx = {i["name"] for i in insp.get_indexes("messages")}
    assert "ix_messages_session_id" in idx


def test_append_and_list_all_ascending(session):
    """追加三条后按时间序读回；role/content 原样保留。"""
    repo = MessageRepo(session)
    repo.append("s1", "user", "你好")
    repo.append("s1", "assistant", "你好！")
    repo.append("s1", "user", "刚才说了什么")
    repo.append("s2", "user", "别的会话")  # 隔离性
    rows = repo.list_all("s1")
    assert [(r.role, r.content) for r in rows] == [
        ("user", "你好"), ("assistant", "你好！"), ("user", "刚才说了什么"),
    ]


def test_replace_all_rewrites_history(session):
    """压缩回写：清空旧行并按 (role, content) 重建。"""
    repo = MessageRepo(session)
    repo.append("s1", "user", "旧1")
    repo.append("s1", "assistant", "旧2")
    repo.replace_all("s1", [("system", "[已压缩] 摘要"), ("user", "近1")])
    rows = repo.list_all("s1")
    assert [(r.role, r.content) for r in rows] == [
        ("system", "[已压缩] 摘要"), ("user", "近1"),
    ]
```

- [ ] **Step 2: 跑测试确认红**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_message_repo.py --basetemp=.pytest_tmp -q`
Expected: FAIL（`ModuleNotFoundError: persistence.repositories.message_repo`）

- [ ] **Step 3: 实现模型 + 迁移 + 仓储**

`persistence/models.py` 文件末尾追加：

```python
class MessageRow(Base):
    """长会话记忆：私聊多轮消息持久化（2026-09-08 spec）。

    role 取值 user/assistant/system（system 仅压缩摘要行）；
    session_id 为 sessions 的逻辑外键（不加物理约束，与项目风格一致）。
    """
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
```

创建 `migrations/versions/0003_messages.py`：

```python
"""长会话记忆：messages 表（2026-09-08 spec）。"""
import sqlalchemy as sa
from alembic import op

revision = "0003_messages"
down_revision = "0002_phase2_to_phase9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建 messages 表 + session_id 索引。"""
    op.create_table(
        "messages",
        sa.Column("message_id", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])


def downgrade() -> None:
    """回滚：删索引与表。"""
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
```

创建 `persistence/repositories/message_repo.py`：

```python
"""messages 表仓储：长会话记忆的消息追加 / 读取 / 压缩回写。"""
from sqlalchemy.orm import Session

from persistence.models import MessageRow
from shared.ulid_ import new_ulid


class MessageRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, session_id: str, role: str, content: str) -> MessageRow:
        """追加一条消息（flush，commit 时机由调用方决定）。"""
        row = MessageRow(message_id=new_ulid(), session_id=session_id,
                         role=role, content=content)
        self.session.add(row)
        self.session.flush()
        return row

    def list_all(self, session_id: str) -> list[MessageRow]:
        """按创建时间升序取会话全部消息（message_id ULID 单调作次序兜底）。"""
        return list(
            self.session.query(MessageRow)
            .filter_by(session_id=session_id)
            .order_by(MessageRow.created_at, MessageRow.message_id)
            .all()
        )

    def replace_all(self, session_id: str, rows: list[tuple[str, str]]) -> None:
        """压缩回写：清空会话全部消息并按 (role, content) 重建。"""
        self.session.query(MessageRow).filter_by(session_id=session_id).delete()
        for role, content in rows:
            self.session.add(MessageRow(
                message_id=new_ulid(), session_id=session_id,
                role=role, content=content))
        self.session.flush()
```

- [ ] **Step 4: 跑测试确认绿**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_message_repo.py --basetemp=.pytest_tmp -q`
Expected: 3 passed

- [ ] **Step 5: 迁移在 pg 真库执行并验证**

Run（pg 容器起着的前提下）:
`.\.venv\Scripts\python.exe -m alembic upgrade head`
Expected: 输出含 `Running upgrade 0002_phase2_to_phase9 -> 0003_messages`；随后
`docker exec feishu-agent-pg psql -U postgres -d agent_dev -c "\d messages"` 见表与索引。

- [ ] **Step 6: Commit**

```bash
git add persistence/models.py migrations/versions/0003_messages.py persistence/repositories/message_repo.py tests/unit/test_message_repo.py
git commit -m "长会话记忆 Task1：messages 表 + 迁移 0003 + MessageRepo（真库 3 测试绿）"
```

---

### Task 2: ContextCompressor.summarize_only

**Files:**
- Modify: `orchestrator/runtime/context_compressor.py`（类内追加方法）
- Test: `tests/unit/test_context_compressor.py`

- [ ] **Step 1: 写失败测试（追加到现有文件末尾）**

```python
def test_summarize_only_calls_llm():
    """freeze 前强制摘要：直接调 LLM，不做 ratio 判断。"""
    llm = MagicMock()
    llm.call.return_value = "摘要：讨论了单细胞质控"
    comp = ContextCompressor(
        llm_router=llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_budget=1000,
    )
    msgs = [ChatMessage(role="user", content="qc 怎么做"),
            ChatMessage(role="assistant", content="先跑 sc_qc")]
    out = comp.summarize_only(msgs)
    assert out == "摘要：讨论了单细胞质控"
    llm.call.assert_called_once()
    assert llm.call.kwargs["role"] == "context_compressor"


def test_summarize_only_empty_messages():
    """空历史不调用 LLM，直接返回空串。"""
    llm = MagicMock()
    comp = ContextCompressor(
        llm_router=llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_budget=1000,
    )
    assert comp.summarize_only([]) == ""
    llm.call.assert_not_called()
```

- [ ] **Step 2: 跑测试确认红**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_context_compressor.py -k summarize --basetemp=.pytest_tmp -q`
Expected: FAIL（`AttributeError: ... no attribute 'summarize_only'`）

- [ ] **Step 3: 实现（context_compressor.py 类内追加，maybe_compress 之后）**

```python
    def summarize_only(self, messages: list[ChatMessage]) -> str:
        """不做 ratio 判断，直接把 messages 压缩成 500 字内摘要（freeze 前调用）。

        空历史直接返回空串；LLM 异常向调用方传播（由 ChatMemory 降级处理）。
        """
        if not messages:
            return ""
        prompt = (
            "将以下对话压缩到500 字以内：\n\n"
            + "\n".join(f"[{m.role}] {m.content}" for m in messages)
        )
        return self.llm_router.call(role="context_compressor", prompt=prompt)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_context_compressor.py --basetemp=.pytest_tmp -q`
Expected: 全部 passed（存量 3 + 新增 2）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/runtime/context_compressor.py tests/unit/test_context_compressor.py
git commit -m "长会话记忆 Task2：ContextCompressor.summarize_only（freeze 前强制摘要）"
```

---

### Task 3: ChatMemory 编排服务

**Files:**
- Create: `orchestrator/chat_memory.py`
- Test: `tests/unit/test_chat_memory.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_chat_memory.py`：

```python
"""长会话记忆：ChatMemory 编排单测（mock 边界，6 用例）。"""
from unittest.mock import MagicMock

from shared.errors import FreezeRequired
from shared.schemas import ChatMessage

from orchestrator.chat_memory import ChatMemory


def _row(role, content):
    """模拟 MessageRow（只取 role/content 两个属性）。"""
    r = MagicMock()
    r.role, r.content = role, content
    return r


def _memory(*, rows=(), compress_out=None, freeze=False, summary="摘要"):
    """装配 mock 边界的 ChatMemory；freeze=True 时 compressor 抛 FreezeRequired。"""
    repo = MagicMock()
    repo.list_all.return_value = list(rows)
    comp = MagicMock()
    hist = [ChatMessage(role=r.role, content=r.content) for r in rows]
    if freeze:
        comp.maybe_compress.side_effect = FreezeRequired("ratio 0.97 >= freeze 0.95")
        comp.summarize_only.return_value = summary
        comp.estimate_tokens.return_value = 194
        comp.token_budget = 200
    else:
        comp.maybe_compress.return_value = (
            compress_out if compress_out is not None else hist
        )
    svc = MagicMock()
    svc.freeze_session.return_value = "new_sid"
    im = MagicMock()
    return ChatMemory(message_repo=repo, compressor=comp,
                      session_service=svc, im=im), repo, comp, svc, im


def test_prepare_normal_passthrough():
    """无压缩：历史原样透传，会话不变、不回写。"""
    mem, repo, comp, svc, im = _memory(rows=[_row("user", "hi"), _row("assistant", "你好")])
    history, sid, frozen = mem.prepare("s1", "c1")
    assert [(m.role, m.content) for m in history] == [("user", "hi"), ("assistant", "你好")]
    assert sid == "s1" and frozen is False
    repo.replace_all.assert_not_called()


def test_prepare_compress_writes_back():
    """发生压缩（返回新列表）：replace_all 回写压缩态，返回压缩后历史。"""
    rows = [_row("user", f"m{i}") for i in range(8)]
    compressed = [ChatMessage(role="system", content="[已压缩] 前文摘要"),
                  ChatMessage(role="user", "m7")]
    mem, repo, comp, svc, im = _memory(rows=rows, compress_out=compressed)
    history, sid, frozen = mem.prepare("s1", "c1")
    assert history == compressed and frozen is False
    repo.replace_all.assert_called_once_with(
        "s1", [("system", "[已压缩] 前文摘要"), ("user", "m7")])


def test_prepare_freeze_full_orchestration():
    """冻结全链路：摘要 → freeze_session → 新会话播摘要行 → 通知 → 返回新会话。"""
    rows = [_row("user", "m1"), _row("assistant", "r1")]
    mem, repo, comp, svc, im = _memory(rows=rows, freeze=True, summary="讨论过质控")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert sid == "new_sid" and frozen is True
    svc.freeze_session.assert_called_once()
    kw = svc.freeze_session.call_args.kwargs
    assert kw["session_id"] == "s1" and kw["summary"] == "讨论过质控"
    assert abs(kw["trigger_ratio"] - 0.97) < 1e-6
    repo.append.assert_called_once_with("new_sid", "system", "[已压缩] 讨论过质控")
    im.reply.assert_called_once()
    assert "新会话" in im.reply.call_args.args[1]
    assert [(m.role, m.content) for m in history] == [("system", "[已压缩] 讨论过质控")]


def test_prepare_freeze_summary_failure_degrades():
    """摘要 LLM 失败：空摘要冻结，不播摘要行，仍正常开新会话。"""
    mem, repo, comp, svc, im = _memory(rows=[_row("user", "m1")], freeze=True)
    comp.summarize_only.side_effect = RuntimeError("llm down")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert sid == "new_sid" and frozen is True and history == []
    assert svc.freeze_session.call_args.kwargs["summary"] == ""
    repo.append.assert_not_called()


def test_prepare_load_failure_degrades_to_no_memory():
    """DB 读失败：降级为空历史，会话不变，不抛异常。"""
    mem, repo, comp, svc, im = _memory()
    repo.list_all.side_effect = RuntimeError("db down")
    history, sid, frozen = mem.prepare("s1", "c1")
    assert (history, sid, frozen) == ([], "s1", False)
    comp.maybe_compress.assert_not_called()


def test_append_turn_and_clear():
    """append_turn 双写 user/assistant；clear 走 freeze_session(summary 空, ratio 0)。"""
    mem, repo, comp, svc, im = _memory()
    mem.append_turn("s1", "你好", "你好！")
    assert repo.append.call_args_list[0].args == ("s1", "user", "你好")
    assert repo.append.call_args_list[1].args == ("s1", "assistant", "你好！")
    new_sid = mem.clear("s1")
    assert new_sid == "new_sid"
    kw = svc.freeze_session.call_args.kwargs
    assert kw == {"session_id": "s1", "summary": "", "trigger_ratio": 0.0}
```

- [ ] **Step 2: 跑测试确认红**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_chat_memory.py --basetemp=.pytest_tmp -q`
Expected: FAIL（`ModuleNotFoundError: orchestrator.chat_memory`）

- [ ] **Step 3: 实现 `orchestrator/chat_memory.py`**

```python
"""长会话记忆编排：历史装配 / 压缩回写 / 冻结续跑 / 手动清空（2026-09-08 spec）。

设计要点：一切外部故障（DB、压缩/摘要 LLM）降级处理，绝不阻断聊天主路径。
"""
from __future__ import annotations

import logging
from typing import Literal, cast

from shared.errors import FreezeRequired
from shared.schemas import ChatMessage

logger = logging.getLogger(__name__)

# messages 表只会写入这三种 role；DB 读回的 str 收窄为 ChatMessage 的 Literal
_ChatRole = Literal["system", "user", "assistant"]


class ChatMemory:
    """私聊多轮记忆：prepare 装配历史（必要时压缩/冻结），append_turn 落库。"""

    def __init__(self, *, message_repo, compressor, session_service, im,
                 audit_repo=None) -> None:
        self.message_repo = message_repo
        self.compressor = compressor
        self.session_service = session_service
        self.im = im
        self.audit_repo = audit_repo

    def prepare(self, session_id: str, chat_id: str) -> tuple[list[ChatMessage], str, bool]:
        """读历史并做压缩/冻结编排。

        返回 (history, 生效 session_id, 是否发生冻结)；
        发生冻结时 history 为新会话的摘要种子行（可能为空）。
        """
        try:
            rows = self.message_repo.list_all(session_id)
        except Exception as e:
            # 降级：记忆失效不阻断聊天
            logger.warning("chat_memory load failed: session=%s err=%s", session_id, e)
            return [], session_id, False
        history = [ChatMessage(role=cast(_ChatRole, r.role), content=r.content)
                   for r in rows]
        try:
            compressed = self.compressor.maybe_compress(history)
        except FreezeRequired:
            return self._freeze(session_id, chat_id, history)
        except Exception as e:
            # 压缩 LLM 失败降级：本轮不压缩继续
            logger.warning("chat_memory compress failed: session=%s err=%s",
                           session_id, e)
            return history, session_id, False
        if compressed is not history:
            # 发生了压缩：回写 DB，下次 load 即压缩态
            try:
                self.message_repo.replace_all(
                    session_id, [(m.role, m.content) for m in compressed])
            except Exception as e:
                logger.warning("chat_memory writeback failed: session=%s err=%s",
                               session_id, e)
        return compressed, session_id, False

    def _freeze(self, session_id: str, chat_id: str,
                history: list[ChatMessage]) -> tuple[list[ChatMessage], str, bool]:
        """FreezeRequired 编排：摘要 → freeze_session → 新会话播摘要 → 通知。"""
        try:
            summary = self.compressor.summarize_only(history)
        except Exception as e:
            # 摘要失败降级：空摘要冻结，新会话从零开始
            logger.warning("chat_memory freeze-summary failed: session=%s err=%s",
                           session_id, e)
            summary = ""
        budget = self.compressor.token_budget
        ratio = self.compressor.estimate_tokens(history) / budget if budget else 0.0
        new_sid = self.session_service.freeze_session(
            session_id=session_id, summary=summary, trigger_ratio=ratio)
        seed: list[ChatMessage] = []
        if summary:
            text = f"[已压缩] {summary}"
            try:
                self.message_repo.append(new_sid, "system", text)
            except Exception as e:
                logger.warning("chat_memory seed failed: session=%s err=%s",
                               new_sid, e)
            seed = [ChatMessage(role="system", content=text)]
        self.im.reply(chat_id, "[系统] 上下文已满，已开启新会话（历史摘要已继承）")
        return seed, new_sid, True

    def append_turn(self, session_id: str, user_text: str, reply_text: str) -> None:
        """双写 user/assistant 两条；失败仅记日志（崩溃最多丢一轮记忆，可接受）。"""
        try:
            self.message_repo.append(session_id, "user", user_text)
            self.message_repo.append(session_id, "assistant", reply_text)
        except Exception as e:
            logger.warning("chat_memory append failed: session=%s err=%s",
                           session_id, e)

    def clear(self, session_id: str) -> str:
        """/clear：手动冻结开新会话（无摘要继承）。返回新 session_id。"""
        return self.session_service.freeze_session(
            session_id=session_id, summary="", trigger_ratio=0.0)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_chat_memory.py --basetemp=.pytest_tmp -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator/chat_memory.py tests/unit/test_chat_memory.py
git commit -m "长会话记忆 Task3：ChatMemory 编排服务（装配/压缩回写/冻结续跑/clear，6 测试绿）"
```

---

### Task 4: Orchestrator 接线（/clear + 闲聊路径 + 类型注解）

**Files:**
- Modify: `orchestrator/app.py`（4 处，见 Step 3）
- Test: `tests/integration/test_chat_memory_flow.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/integration/test_chat_memory_flow.py`：

```python
"""长会话记忆：process() 接线集成测试（真 sqlite + mock LLM/适配层）。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app import Orchestrator
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from persistence.models import Base
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from persistence.repositories.task_repo import TaskRepo
from shared.schemas import ChatMessage, IncomingMessage


@pytest.fixture
def orch():
    """带内存 SQLite + mock LLM/IM 的 Orchestrator（test_message_flow 同款构造）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    session_repo = SessionRepo(s)
    session_svc = SessionService(session_repo)
    task_svc = TaskService(TaskRepo(s), AuditRepo(s))
    bind_svc = BindDocService(session_svc, AuditRepo(s), ttl_sec=1800,
                              session_repo=session_repo)
    doc_adapter = MagicMock()
    doc_adapter.append_plain_text.return_value = "blk_x"
    doc_write_svc = DocWriteService(session_repo, DocWriteRepo(s), doc_adapter)

    llm_router = MagicMock()
    llm_router.chat.return_value = "你好，我是 AI 助手。"
    im_adapter = MagicMock()
    im_adapter.reply.return_value = "om_reply"

    o = Orchestrator(
        llm_router=llm_router, session_service=session_svc,
        task_service=task_svc, bind_doc_service=bind_svc,
        doc_write_service=doc_write_svc, im_adapter=im_adapter,
    )
    yield o, llm_router, im_adapter
    s.close()


def _incoming(text, mid="om_1"):
    return IncomingMessage(message_id=mid, chat_id="oc_1",
                           sender_open_id="ou_1", text=text, chat_type="p2p")


def test_process_injects_history_and_appends_turn(orch):
    """装配 chat_memory 后：LLM 收到 system+历史+当前；回复后双写落库。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.prepare.return_value = (
        [ChatMessage(role="user", content="前一轮"),
         ChatMessage(role="assistant", content="前一答")],
        "sid_x", False,
    )
    o.chat_memory = memory
    result = o.process(_incoming("刚才说了什么"))
    assert result["status"] == "success"
    sent = llm_router.chat.call_args.args[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1].content == "前一轮" and sent[-1].content == "刚才说了什么"
    memory.append_turn.assert_called_once_with(
        result["session_id"], "刚才说了什么", "你好，我是 AI 助手。")


def test_process_freeze_continues_with_new_session(orch):
    """freeze 后：返回/bound_doc 查询用新 session_id，文档链路不受影响。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.prepare.return_value = (
        [ChatMessage(role="system", content="[已压缩] 摘要")], "sid_new", True,
    )
    o.chat_memory = memory
    result = o.process(_incoming("继续"))
    assert result["status"] == "success"
    assert result["session_id"] == "sid_new"
    sent = llm_router.chat.call_args.args[0]
    assert sent[1].content == "[已压缩] 摘要"
    memory.append_turn.assert_called_once_with(
        "sid_new", "继续", "你好，我是 AI 助手。")


def test_process_without_memory_unchanged(orch):
    """未装配 chat_memory（旧构造）：无记忆两消息行为不变（回归护栏）。"""
    o, llm_router, im_adapter = orch
    result = o.process(_incoming("hello"))
    assert result["status"] == "success"
    sent = llm_router.chat.call_args.args[0]
    assert len(sent) == 2 and sent[-1].content == "hello"


def test_clear_command(orch):
    """/clear：走 chat_memory.clear，回复提示，不进 LLM。"""
    o, llm_router, im_adapter = orch
    memory = MagicMock()
    memory.clear.return_value = "sid_new"
    o.chat_memory = memory
    result = o.process(_incoming("/clear"))
    assert result["status"] == "cleared"
    memory.clear.assert_called_once()
    llm_router.chat.assert_not_called()
    assert "新会话" in im_adapter.reply.call_args.args[1]
```

- [ ] **Step 2: 跑测试确认红**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_chat_memory_flow.py --basetemp=.pytest_tmp -q`
Expected: 4 个用例中 test_process_injects_history_and_appends_turn 与 test_clear_command FAIL
（chat_memory 未接入/clear 未路由）；另两个碰巧可能 PASS——以 2 红为准

- [ ] **Step 3: 实现 app.py 四处改动**

**① TYPE_CHECKING 导入（L53 后追加一行）：**

```python
    from orchestrator.templates.version_service import VersionService
    from orchestrator.chat_memory import ChatMemory
```

（ruff isort 顺序：chat_memory 按字母序应在 approval_broker 行附近；
实际插入 `from orchestrator.approval_broker import ApprovalBroker` 之后、
`from orchestrator.coding.coding_runner import CodingRunner` 之前：
`    from orchestrator.chat_memory import ChatMemory`）

**② 类级注解（L81 `coding_runner: CodingRunner` 行后追加）：**

```python
    model_switch_service: ModelSwitchService
    # 长会话记忆（2026-09-08 spec）：runtime.py 装配；未装配时闲聊为无记忆单轮
    chat_memory: ChatMemory
```

**③ /clear 路由（process() 内 L229 `return {"status": "renew_bind", ...}` 块之后插入）：**

```python
        # 1.25 /clear 指令：手动冻结当前会话开新会话（长会话记忆重置入口）
        if incoming.text.strip() == "/clear":
            memory = getattr(self, "chat_memory", None)
            if memory is None:
                self.im.reply(incoming.chat_id, "[提示] 长会话记忆未装配")
                return {"status": "clear_unavailable"}
            clear_sid = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            memory.clear(clear_sid)
            self.im.reply(incoming.chat_id, "[成功] 已开启新会话，历史已清空")
            return {"status": "cleared", "session_id": clear_sid}
```

**④ 闲聊路径接入（替换 L321-326 的 LLM 调用段）：**

旧代码：

```python
        # 3. LLM 生成回复
        try:
            reply_text = self.llm.chat([
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=incoming.text),
            ])
```

新代码：

```python
        # 3. LLM 生成回复（长会话记忆：装配了 chat_memory 时注入历史，
        #    压缩/冻结由 ChatMemory 编排；freeze 后 session_id 换为新会话，
        #    下游 bound_doc/写文档随新会话走——绑定已被 freeze_session 继承）
        memory = getattr(self, "chat_memory", None)
        if memory is None:
            messages = [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=incoming.text),
            ]
        else:
            history, session_id, _frozen = memory.prepare(
                session_id=session_id, chat_id=incoming.chat_id)
            messages = ([ChatMessage(role="system", content=SYSTEM_PROMPT)]
                        + history
                        + [ChatMessage(role="user", content=incoming.text)])
        try:
            reply_text = self.llm.chat(messages)
```

**⑤ append_turn 落库（L340 `self.im.reply(incoming.chat_id, reply_text)` 之后插入）：**

```python
        # 3.5 记忆落库（回复先行，写库在后：崩溃最多丢一轮记忆，可接受）
        if memory is not None:
            memory.append_turn(session_id, incoming.text, reply_text)
```

**⑥ 未知命令提示（L285-289 文案加 /clear）：** 把
`"/bind-doc <doc_id>（绑定文档）· /template-list（我的模板）\n"` 改为
`"/bind-doc <doc_id>（绑定文档）· /clear（清空会话记忆）· /template-list（我的模板）\n"`

**⑦ process_phase3 docstring 更新（L525-529 区域）：** 把"注：ContextCompressor/freeze 未接线（Phase 3.1 归档挂起 2026-09-08）——"整段改为：

```python
        - 长会话记忆（2026-09-08 落地）：process() 闲聊路径经 ChatMemory
          注入历史，压缩/冻结由 ContextCompressor + freeze_session 执行
```

- [ ] **Step 4: 跑测试确认绿**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_chat_memory_flow.py --basetemp=.pytest_tmp -q`
Expected: 4 passed

- [ ] **Step 5: mypy 双平台 + ruff 自检**

Run: `.\.venv\Scripts\python.exe -m mypy; .\.venv\Scripts\python.exe -m mypy --platform linux; .\.venv\Scripts\ruff.exe check .`
Expected: 两个 Success + All checks passed

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app.py tests/integration/test_chat_memory_flow.py
git commit -m "长会话记忆 Task4：Orchestrator 接线（/clear 路由 + 闲聊历史注入 + freeze 续跑）"
```

---

### Task 5: runtime.py 装配

**Files:**
- Modify: `gateway/runtime.py`（orch 构造后追加装配块）

- [ ] **Step 1: 装配代码**

在 `gateway/runtime.py` 的 `orch = Orchestrator(...)` 构造块（L175-180）之后、
`# --- Phase 5-6：模板库 ---` 注释之前插入：

```python
    # --- 长会话记忆（2026-09-08 spec）：ChatMemory 装配 ---
    from orchestrator.chat_memory import ChatMemory
    from orchestrator.runtime.context_compressor import ContextCompressor
    from persistence.repositories.message_repo import MessageRepo

    context_compressor = ContextCompressor(
        llm_router=llm, session_repo=SessionRepo(session), audit_repo=audit_repo,
        token_budget=settings.context_token_budget,
        compress_trigger_ratio=settings.context_compress_trigger_ratio,
        freeze_trigger_ratio=settings.context_freeze_trigger_ratio,
        preserve_recent_n=settings.context_preserve_recent_n,
    )
    orch.chat_memory = ChatMemory(
        message_repo=MessageRepo(session), compressor=context_compressor,
        session_service=session_service, im=im, audit_repo=audit_repo,
    )
```

（先确认 runtime.py 中这三个名字的实参变量：LLMRouter 实例为 `llm`、
SQLAlchemy session 为 `session`、SessionService 实例为 `session_service`、
IMAdapter 为 `im`——若实际变量名不同以现场为准；import 放函数顶部既有
import 区还是就地，遵循该文件现有风格。）

- [ ] **Step 2: 门禁自检 + 全量回归**

Run: `.\.venv\Scripts\python.exe -m mypy; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\python.exe -m pytest --basetemp=.pytest_tmp -q`
Expected: Success + All checks passed + 全量 passed（基线 1165 + 本轮新增 ≈ 1178）

- [ ] **Step 3: Commit**

```bash
git add gateway/runtime.py
git commit -m "长会话记忆 Task5：runtime.py 装配 ChatMemory + ContextCompressor"
```

---

### Task 6: 推送 + CI 验证

- [ ] **Step 1: 推送（pre-push 门禁自动全量跑）**

Run: `git push origin master`
Expected: `[PASS] 质量门全部通过`

- [ ] **Step 2: CI 确认**

Run: 等待约 3 分钟后 `.\.venv\Scripts\python.exe scripts\_ci_log.py`
Expected: `run <id>: completed / success`

---

### Task 7: 真机验收（需用户在飞书配合）

- [ ] **Step 1: 压低阈值重启 ws_client**

`.env` 临时追加（或修改）：

```
CONTEXT_TOKEN_BUDGET=200
CONTEXT_FREEZE_TRIGGER_RATIO=0.5
CONTEXT_COMPRESS_TRIGGER_RATIO=0.3
```

重启：`powershell -File scripts\start.ps1`（预检→拉起 pg→后台启动→60s 日志确认）

- [ ] **Step 2: 飞书私聊触发**

私聊机器人连发数条消息（每条几十字即可超 200 token 预算）：
1. 第 1-2 条：正常多轮（追问"刚才我说了什么"验证历史注入生效）
2. 继续发 → 触发压缩（日志 `compress_history` 审计）
3. 再发 → 触发冻结：收到"[系统] 上下文已满，已开启新会话"，随后本条消息仍得到正常回复
4. 发 `/clear` → 收到"[成功] 已开启新会话，历史已清空"

- [ ] **Step 3: 硬证据核对**

- ws_client 日志：`compress_history` / freeze 通知发送记录
- pg 核对：
  `docker exec feishu-agent-pg psql -U postgres -d agent_dev -c "select session_id,status,origin_session_id from sessions order by created_at desc limit 5;"`
  → 旧行 archived + 新行 active 且 origin_session_id 指回旧行
  `docker exec feishu-agent-pg psql -U postgres -d agent_dev -c "select role,left(content,20),created_at from messages order by created_at desc limit 10;"`
  → 见 user/assistant 交替与 system 摘要行
  `docker exec feishu-agent-pg psql -U postgres -d agent_dev -c "select origin_session_id,new_session_id,trigger_ratio from session_freezes;"`
  → 冻结记录（trigger_ratio≈0.5+ 一条、/clear 的 0.0 一条）

- [ ] **Step 4: 还原阈值并重启**

删 `.env` 三个临时项（或恢复原值）→ `scripts\start.ps1` 重启 → 私聊一条确认正常。

---

### Task 8: 文档沉淀

- [ ] **Step 1: 测试总结**

新建 `测试总结+<时间戳>.md`：Task1-5 测试记录、真机验收结果（成功/失败 + 证据）、
遇到的问题（若 SearchReplace 假成功复现须记录）。

- [ ] **Step 2: ROADMAP 变更记录**

追加行：长会话记忆落地（spec/plan 链接、五个 Task 摘要、真机验收结论、
Phase 3.1 由挂起转为正式落地）。

- [ ] **Step 3: 提交推送**

```bash
git add -A docs 测试总结*.md
git commit -m "长会话记忆文档收官：测试总结 + ROADMAP 变更记录"
git push origin master
```

---

## Self-Review 记录

- **Spec 覆盖**：组件清单（Task1/2/3）、数据流与 freeze 时序（Task3/4）、/clear（Task4③）、
  错误处理降级（Task3 用例 4/5）、测试策略（各 Task 红绿 + Task7 真机）、
  YAGNI 范围外未实现 ✓
- **偏差修订**：spec 组件清单 5（新增 context_token_budget）→ 实际 settings.py L56 已有，
  连同 L65-67 三个字段全部复用零新增；spec 的 delete_many → MessageRepo.replace_all
  （压缩回写一步完成，语义更贴合）
- **类型一致**：prepare 返回 `tuple[list[ChatMessage], str, bool]` 在 Task3 定义、
  Task4 使用一致；freeze_session 关键字签名与 session_service.py 现状一致；
  ChatMessage role Literal 收窄用 `cast(_ChatRole, ...)` 保证 mypy 绿
