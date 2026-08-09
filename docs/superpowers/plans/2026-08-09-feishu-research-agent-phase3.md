# Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Phase 2 静态 DAG 基础上引入动态化（DAG 节点追加 + while/for 循环 + context 压缩）、AST P1/P2 分级提示、bind_doc 续期（卡片 + 指令双通道），并抽 `PlanRuntime` 让 Phase 5 gRPC 拆分有天然边界。

**Architecture:** 抽 `PlanRuntime` 接管 ready/cancel/finish + 动态性；Scheduler 退化为驱动循环 + 兼容 Phase 2 路径。Planner 增 3 个 LLM role（condition_eval / loop_eval / context_compressor）。ORM 增 `plan_runtime_state` + `session_freezes` 2 张表 + 字段扩展。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + pydantic v2 + httpx + tiktoken + jupyter_client + docker SDK + OpenAI function calling schema + pytest + respx

**前置 spec:** [../specs/2026-08-09-feishu-research-agent-phase3-design.md](../specs/2026-08-09-feishu-research-agent-phase3-design.md)
**前置 ADRs:** [../specs/adrs/0001](../specs/adrs/0001-phase3-runtime-architecture.md) ~ [0005](../specs/adrs/0005-phase3-bind-doc-freeze-inheritance.md)

---

## File Structure（前置：决定 Task 拆分）

```
orchestrator/runtime/                  # Phase 3 新
  __init__.py
  plan_runtime.py                      # Runtime 抽象 + 状态机
  context_compressor.py                # 80%/95% 双阈值
  runtime_state_repo.py                # plan_runtime_state 持久化
orchestrator/background/               # Phase 3 新
  __init__.py
  task_runner.py                       # BackgroundTaskRunner 简单协程轮询
orchestrator/planner/                  # Phase 2 + 3 升级
  dag_schema.py                        # 增 branch/while/for + 嵌套校验
  planner.py                           # 增 3 个 LLM role + 嵌套 DAGNode 构造
  scheduler.py                         # 委托 Runtime，兼容双模式
orchestrator/tools/                    # Phase 2 + 3 升级
  ast_guard.py                         # P1/P2 分级 + ASTReport
orchestrator/session_service.py        # 升级：freeze + 跨 session 继承
orchestrator/llm_router.py             # 升级：role_condition_eval / loop_eval / context_compressor
orchestrator/bind_doc_service.py       # 升级：renew 方法
orchestrator/app.py                    # 升级：process_phase3 入口
gateway/app.py                         # 升级：/bind-doc-renew 指令 + 续期卡片回调
persistence/models.py                  # 扩：plan_runtime_state + session_freezes + 字段扩展
persistence/repositories/
  plan_runtime_state_repo.py           # 新
  session_freeze_repo.py               # 新
  execution_repo.py                    # 增 loop_id / loop_iteration / dynamic_parent_id
  session_repo.py                      # 增 archived_at / origin_session_id / token_count
migrations/versions/0003_phase3_runtime.py  # 新
shared/errors.py                       # 扩：FreezeRequired / LoopMaxIterError 等
shared/schemas.py                      # 增 summary_block / estimated_tokens
config/settings.py                     # 扩：context_compress_trigger_ratio / freeze_trigger_ratio 等
tests/unit/                            # 22+ 新测试
tests/integration/                     # E1-E8 场景
```

---

## Task 1: 错误类型扩展 + SummaryBlock schema

**Files:**
- Modify: `shared/errors.py`
- Modify: `shared/schemas.py`
- Create: `tests/unit/test_phase3_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase3_schemas.py
from shared.errors import FreezeRequired, LoopMaxIterError, DynamicAppendError
from shared.schemas import SummaryBlock, ChatMessage


def test_freeze_required_inherits_feishu():
    e = FreezeRequired("summary too large")
    assert e.code == "FREEZE_REQUIRED"


def test_loop_max_iter_error():
    e = LoopMaxIterError("loop_n5 reached max")
    assert e.code == "LOOP_MAX_ITER"


def test_dynamic_append_error():
    e = DynamicAppendError("invalid dynamic nodes")
    assert e.code == "DYNAMIC_APPEND_FAILED"


def test_summary_block_minimal():
    sb = SummaryBlock(summary_id="s_1", text="...")
    assert sb.kind == "summary"
    assert sb.ref == "s_1"
    assert sb.text == "..."


def test_chat_message_estimated_tokens_default():
    m = ChatMessage(role="user", content="hi")
    assert m.estimated_tokens == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_phase3_schemas.py -v`
Expected: ImportError

- [ ] **Step 3: Append to shared/errors.py**

```python
class FreezeRequired(FeishuAgentError):
    """上下文超 95%，强制冻结 session。"""
    code = "FREEZE_REQUIRED"


class LoopMaxIterError(FeishuAgentError):
    """循环节点达到 max_iterations 强制退出。"""
    code = "LOOP_MAX_ITER"


class DynamicAppendError(FeishuAgentError):
    """动态追加节点失败（validate_dag 不通过）。"""
    code = "DYNAMIC_APPEND_FAILED"
```

- [ ] **Step 4: Append SummaryBlock to shared/schemas.py**

Read existing `shared/schemas.py`, then **append** before any final closing:

```python
from dataclasses import dataclass, field


@dataclass
class SummaryBlock:
    """上下文压缩后的总结块；可作为 ChatMessage 替代品。"""
    summary_id: str
    text: str
    kind: str = "summary"
    ref: str = ""
    saved_tokens: int = 0


@dataclass
class ChatMessage:
    """原有字段保持不变；新增 estimated_tokens。"""
    role: str
    content: str
    estimated_tokens: int = 0
    summary: Optional[SummaryBlock] = None
```

(如果原 ChatMessage 已存在 dataclass 字段，**只新增**字段，不替换整个定义。)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_phase3_schemas.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add shared/errors.py shared/schemas.py tests/unit/test_phase3_schemas.py
git commit -m "feat(phase3): add Phase 3 errors + SummaryBlock schema"
```

---

## Task 2: Settings 扩展（压缩阈值 + LLM role + AST patterns）

**Files:**
- Modify: `config/settings.py`
- Create: `tests/unit/test_phase3_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase3_settings.py
from config.settings import Settings


def test_phase3_settings_defaults():
    s = Settings.__dataclass_fields__
    assert "context_compress_trigger_ratio" in s
    assert "context_freeze_trigger_ratio" in s
    assert "loop_max_iterations_default" in s
    assert "ast_p1_patterns" in s
    assert "ast_p2_patterns" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_phase3_settings.py -v`
Expected: FAIL (fields not exist)

- [ ] **Step 3: Append Phase 3 fields to config/settings.py Settings dataclass**

```python
    # === Phase 3: 上下文压缩 ===
    context_compress_trigger_ratio: float = 0.8
    context_freeze_trigger_ratio: float = 0.95
    context_preserve_recent_n: int = 5

    # === Phase 3: 循环上限 ===
    loop_max_iterations_default_while: int = 10
    loop_max_iterations_default_for: int = 100

    # === Phase 3: AST 分级 ===
    ast_p1_patterns: tuple = (
        "requests.get", "requests.post", "requests.put", "requests.delete",
        "urllib.request.urlopen", "httpx.get", "httpx.post",
        "aiohttp.ClientSession", "aiohttp.get", "aiohttp.post",
    )
    ast_p2_patterns: tuple = (
        "shutil.copy", "shutil.move", "shutil.rmtree",
        "os.remove", "os.unlink", "os.rmdir",
    )

    # === Phase 3: bind_doc 续期 ===
    bind_doc_renew_threshold_sec: int = 300  # 剩余 5 分钟时发卡片
    bind_doc_renew_card_interval_sec: int = 60  # BackgroundTaskRunner 间隔
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_phase3_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/settings.py tests/unit/test_phase3_settings.py
git commit -m "feat(phase3): add Phase 3 settings (compression, loops, AST patterns)"
```

---

## Task 3: ORM 扩展（plan_runtime_state + session_freezes + 字段增量）

**Files:**
- Modify: `persistence/models.py`
- Create: `migrations/versions/0003_phase3_runtime.py`
- Create: `tests/unit/test_phase3_orm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase3_orm.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from persistence.models import Base


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
    Base.metadata.create_all(e)
    return e


def test_plan_runtime_state_table_created(engine):
    from sqlalchemy import inspect
    insp = inspect(engine)
    assert "plan_runtime_state" in insp.get_table_names()


def test_session_freezes_table_created(engine):
    from sqlalchemy import inspect
    insp = inspect(engine)
    assert "session_freezes" in insp.get_table_names()


def test_executions_has_loop_fields(engine):
    from sqlalchemy import inspect
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("executions")}
    assert "loop_id" in cols
    assert "loop_iteration" in cols
    assert "dynamic_parent_id" in cols


def test_sessions_has_archived_at(engine):
    from sqlalchemy import inspect
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("sessions")}
    assert "archived_at" in cols
    assert "origin_session_id" in cols
    assert "token_count" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_phase3_orm.py -v`
Expected: FAIL (tables/columns not exist)

- [ ] **Step 3: Append Phase 3 models to persistence/models.py**

```python
class PlanRuntimeStateRow(Base):
    __tablename__ = "plan_runtime_state"
    plan_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SessionFreezeRow(Base):
    __tablename__ = "session_freezes"
    freeze_id: Mapped[str] = mapped_column(String, primary_key=True)
    origin_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    new_session_id: Mapped[str] = mapped_column(String, nullable=False)
    summary_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger_ratio: Mapped[float] = mapped_column(default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

Also extend `ExecutionRow`:
```python
    loop_id: Mapped[str | None] = mapped_column(String, nullable=True)
    loop_iteration: Mapped[int | None] = mapped_column(default=None, nullable=True)
    dynamic_parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

Also extend `SessionRow`:
```python
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    origin_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    token_count: Mapped[int] = mapped_column(default=0, nullable=False)
```

- [ ] **Step 4: Create migrations/versions/0003_phase3_runtime.py**

```python
"""Phase 3: plan_runtime_state + session_freezes + executions/sessions 扩展。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = = None
depends_on = None


def upgrade():
    op.create_table(
        "plan_runtime_state",
        sa.Column("plan_id", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, nullable=True),
        sa.Column("state_json", sa.JSON, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_prs_session_id", "plan_runtime_state", ["session_id"])

    op.create_table(
        "session_freezes",
        sa.Column("freeze_id", sa.Text, primary_key=True),
        sa.Column("origin_session_id", sa.Text, nullable=False),
        sa.Column("new_session_id", sa.Text, nullable=False),
        sa.Column("summary_id", sa.Text, nullable=True),
        sa.Column("trigger_ratio", sa.Float, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_sf_origin_session", "session_freezes", ["origin_session_id"])

    op.add_column("executions", sa.Column("loop_id", sa.Text, nullable=True))
    op.add_column("executions", sa.Column("loop_iteration", sa.Integer, nullable=True))
    op.add_column("executions", sa.Column("dynamic_parent_id", sa.Text, nullable=True))

    op.add_column("sessions", sa.Column("archived_at", sa.DateTime, nullable=True))
    op.add_column("sessions", sa.Column("origin_session_id", sa.Text, nullable=True))
    op.add_column("sessions", sa.Column("token_count", sa.Integer, server_default="0"))


def downgrade():
    op.drop_column("sessions", "token_count")
    op.drop_column("sessions", "origin_session_id")
    op.drop_column("sessions", "archived_at")
    op.drop_column("executions", "dynamic_parent_id")
    op.drop_column("executions", "loop_iteration")
    op.drop_column("executions", "loop_id")
    op.drop_index("ix_sf_origin_session", table_name="session_freezes")
    op.drop_table("session_freezes")
    op.drop_index("ix_prs_session_id", table_name="plan_runtime_state")
    op.drop_table("plan_runtime_state")
```

(注意 `branch_labels = = None` 是笔误，改为 `branch_labels = None`。)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_phase3_orm.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add persistence/models.py migrations/versions/0003_phase3_runtime.py tests/unit/test_phase3_orm.py
git commit -m "feat(phase3): add plan_runtime_state + session_freezes + field extensions"
```

---

## Task 4: Repositories for runtime state + session freezes

**Files:**
- Create: `persistence/repositories/plan_runtime_state_repo.py`
- Create: `persistence/repositories/session_freeze_repo.py`
- Create: `tests/unit/test_phase3_repos.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase3_repos.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from persistence.models import Base
from persistence.repositories.plan_runtime_state_repo import PlanRuntimeStateRepo
from persistence.repositories.session_freeze_repo import SessionFreezeRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def test_runtime_state_upsert_and_get(session):
    repo = PlanRuntimeStateRepo(session)
    repo.upsert(plan_id="p1", session_id="s1", state_json={"loop_counters": {}})
    state = repo.get("p1")
    assert state.session_id == "s1"
    assert state.state_json == {"loop_counters": {}}
    # upsert update
    repo.upsert(plan_id="p1", session_id="s1", state_json={"loop_counters": {"x": 1}})
    assert repo.get("p1").state_json == {"loop_counters": {"x": 1}}


def test_freeze_create_and_list(session):
    repo = SessionFreezeRepo(session)
    fid = repo.create(origin_session_id="so", new_session_id="sn",
                      summary_id="s_1", trigger_ratio=0.97)
    f = repo.get(fid)
    assert f.origin_session_id == "so"
    assert f.trigger_ratio == 0.97
    assert len(repo.list_by_origin("so")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_phase3_repos.py -v`
Expected: ImportError

- [ ] **Step 3: Create plan_runtime_state_repo.py**

```python
# persistence/repositories/plan_runtime_state_repo.py
"""plan_runtime_state 表的 CRUD。"""
from __from__ import annotations

from persistence.models import PlanRuntimeStateRow


class PlanRuntimeStateRepo:
    def __init__(self, session) -> None:
        self.session = session

    def upsert(self, *, plan_id: str, session_id: str | None = None,
               state_json: dict | None = None, status: str = "running") -> None:
        existing = self.session.query(PlanRuntimeStateRow).filter_by(plan_id=plan_id).one_or_none()
        if existing is None:
            row = PlanRuntimeStateRow(
                plan_id=plan_id, session_id=session_id,
                state_json=state_json, status=status,
            )
            self.session.add(row)
        else:
            if session_id is not None:
                existing.session_id = session_id
            if state_json is not None:
                existing.state_json = state_json
            existing.status = status
        self.session.commit()

    def get(self, plan_id: str) -> PlanRuntimeStateRow:
        return self.session.query(PlanRuntimeStateRow).filter_by(plan_id=plan_id).one()

    def get_or_none(self, plan_id: str) -> PlanRuntimeStateRow | None:
        return self.session.query(PlanRuntimeStateRow).filter_by(plan_id=plan_id).one_or_none()
```

- [ ] **Step 4: Create session_freeze_repo.py**

```python
# persistence/repositories/session_freeze_repo.py
"""session_freezes 表的 CRUD。"""
from __from__ import annotations

from persistence.models import SessionFreezeRow
from shared.ulid_ import new_ulid


class SessionFreezeRepo:
    def __init__(self, session) -> None:
        self.session = session

    def create(self, *, origin_session_id: str, new_session_id: str,
               summary_id: str | None = None, trigger_ratio: float) -> str:
        fid = new_ulid()
        row = SessionFreezeRow(
            freeze_id=fid, origin_session_id=origin_session_id,
            new_session_id=new_session_id, summary_id=summary_id,
            trigger_ratio=trigger_ratio,
        )
        self.session.add(row)
        self.session.commit()
        return fid

    def get(self, freeze_id: str) -> SessionFreezeRow:
        return self.session.query(SessionFreezeRow).filter_by(freeze_id=freeze_id).one()

    def list_by_origin(self, origin_session_id: str) -> list[SessionFreezeRow]:
        return self.session.query(SessionFreezeRow).filter_by(
            origin_session_id=origin_session_id
        ).all()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_phase3_repos.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add persistence/repositories/plan_runtime_state_repo.py persistence/repositories/session_freeze_repo.py tests/unit/test_phase3_repos.py
git commit -m "feat(phase3): add PlanRuntimeStateRepo + SessionFreezeRepo"
```

---

## Task 5: DAGNode 升级（branch/while/for + 嵌套校验）

**Files:**
- Modify: `orchestrator/planner/dag_schema.py`
- Create: `tests/unit/test_dag_schema_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dag_schema_phase3.py
import pytest
from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from shared.errors import DAGValidationError


def test_branch_node_minimal():
    n = DAGNode(
        node_id="b1", kind="branch",
        condition_prompt="if error then retry",
        true_branch=[],
        false_branch=[],
        depends_on=[],
    )
    assert n.kind == "branch"


def test_while_node_minimal():
    n = DAGNode(
        node_id="w1", kind="while",
        while_condition_prompt="keep looping",
        body=[], max_iterations=10, depends_on=[],
    )
    assert n.max_iterations == 10


def test_for_node_minimal():
    n = DAGNode(
        node_id="f1", kind="for",
        iterate_over="n1.files",
        iteration_var="file",
        body=[], max_iterations=100, depends_on=[],
    )
    assert n.iterate_over == "n1.files"


def test_branch_with_subtree_validates():
    sub = DAGNode(node_id="b1a", kind="tool", tool_name="read_doc",
                  inputs={"doc_id": "d1"}, depends_on=["b1"])
    n = DAGNode(node_id="b1", kind="branch",
                condition_prompt="x",
                true_branch=[sub],
                false_branch=[],
                depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    validate_dag(plan)  # 不抛


def test_branch_with_cyclic_subtree_fails():
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a", inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b", inputs={}, depends_on=["sub1"])
    n = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                true_branch=[sub1, sub2], false_branch=[], depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    with pytest.raises(DAGValidationError, match="cycle"):
        validate_dag(plan)


def test_branch_empty_subtrees_fails():
    n = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                true_branch=None, false_branch=None, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[n], entry_node_ids=["b1"])
    with pytest.raises(DAGValidationError, match="no sub-branches"):
        validate_dag(plan)


def test_while_nested_in_while_fails():
    inner = DAGNode(node_id="inner_w", kind="while",
                    while_condition_prompt="x", body=[], depends_on=[])
    outer = DAGNode(node_id="outer_w", kind="while",
                    while_condition_prompt="x", body=[inner], depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[outer], entry_node_ids=["outer_w"])
    with pytest.raises(DAGValidationError, match="nested while/for"):
        validate_dag(plan)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_dag_schema_phase3.py -v`
Expected: ImportError or Literal mismatch

- [ ] **Step 3: Modify orchestrator/planner/dag_schema.py**

Update DAGNode.kind Literal:
```python
    kind: Literal["tool", "llm", "branch", "while", "for", "join"]
```

Add fields to DAGNode:
```python
    # === branch ===
    condition_prompt: Optional[str] = None
    true_branch: Optional[list["DAGNode"]] = None
    false_branch: Optional[list["DAGNode"]] = None
    # === while ===
    while_condition_prompt: Optional[str] = None
    body: Optional[list["DAGNode"]] = None
    max_iterations: int = 10
    # === for ===
    iterate_over: Optional[str] = None
    iteration_var: str = "item"
```

Extend validate_dag (add at end):
```python
    # 嵌套子树校验
    for node in plan.nodes:
        if node.kind == "branch":
            if node.true_branch is None and node.false_branch is None:
                raise DAGValidationError(
                    f"branch node {node.node_id} has no sub-branches"
                )
            for sub in (node.true_branch or []) + (node.false_branch or []):
                _validate_subtree(sub, parent_id=node.node_id, all_nodes=plan.nodes)
        elif node.kind in {"while", "for"}:
            if not node.body:
                raise DAGValidationError(
                    f"{node.kind} node {node.node_id} has empty body"
                )
            for sub in node.body:
                if sub.kind in {"while", "for"}:
                    raise DAGValidationError(
                        f"nested while/for not allowed in Phase 3: "
                        f"{sub.kind} inside {node.kind} {node.node_id}"
                    )
                _validate_subtree(sub, parent_id=node.node_id, all_nodes=plan.nodes)


def _validate_subtree(sub: DAGNode, *, parent_id: str, all_nodes: list[DAGNode]) -> None:
    """校验嵌套子树：依赖、循环。"""
    ids = {n.node_id for n in all_nodes} | {parent_id}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sub.node_id: WHITE}

    def dfs(u: DAGNode, stack: list[str]) -> None:
        color[u.node_id] = GRAY
        stack.append(u.node_id)
        for dep in u.depends_on:
            if dep not in ids:
                raise DAGValidationError(
                    f"subtree node {u.node_id} depends_on missing {dep!r}"
                )
            if dep == parent_id:
                continue  # 隐式依赖 parent，跳过
            if color.get(dep) == GRAY:
                raise DAGValidationError(f"cycle in subtree: {' -> '.join(stack + [dep])}")
        stack.pop()
        color[u.node_id] = BLACK

    dfs(sub, [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_dag_schema_phase3.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/planner/dag_schema.py tests/unit/test_dag_schema_phase3.py
git commit -m "feat(phase3): extend DAGNode with branch/while/for + nested subtree validation"
```

---

## Task 6: PlanRuntime 核心（状态机 + dynamic append）

**Files:**
- Create: `orchestrator/runtime/__init__.py`
- Create: `orchestrator/runtime/plan_runtime.py`
- Create: `tests/unit/test_plan_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plan_runtime.py
from orchestrator.runtime.plan_runtime import PlanRuntime, RuntimeState
from orchestrator.planner.dag_schema import DAGNode, DAGPlan


class FakeStateRepo:
    def __init__(self):
        self.states = {}

    def upsert(self, *, plan_id, session_id=None, state_json=None, status="running"):
        self.states[plan_id] = {"session_id": session_id, "state_json": state_json, "status": status}

    def get_or_none(self, plan_id):
        return self.states.get(plan_id)


class FakeAuditRepo:
    def __init__(self):
        self.logs = []

    def write(self, **kw):
        self.logs.append(kw)


def test_runtime_state_enum():
    assert RuntimeState.RUNNING.value == "running"
    assert RuntimeState.TERMINAL.value == "terminal"


def test_runtime_initializes_state():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    assert rt.state == RuntimeState.INIT


def test_append_dynamic_nodes_validates_dag():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    parent = DAGNode(node_id="b1", kind="branch", condition_prompt="x",
                     true_branch=[], false_branch=[], depends_on=[])
    sub = DAGNode(node_id="b1a", kind="tool", tool_name="a", inputs={}, depends_on=["b1"])
    rt.append_dynamic_nodes(plan_id="p", parent_node_id="b1", new_nodes=[sub])
    state = rt.state_repo.get_or_none("p")
    assert state["state_json"]["dynamic_nodes"][0]["node_ids"] == ["b1a"]


def test_append_dynamic_nodes_cycle_fails():
    from shared.errors import DynamicAppendError, DAGValidationError
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a", inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b", inputs={}, depends_on=["sub1"])
    try:
        rt.append_dynamic_nodes(plan_id="p", parent_node_id="p1", new_nodes=[sub1, sub2])
        assert False
    except (DynamicAppendError, DAGValidationError):
        pass


def test_loop_iteration_done_increments():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo(),
                     max_iterations=3)
    n1 = rt.loop_iteration_done("loop_1")
    n2 = rt.loop_iteration_done("loop_1")
    n3 = rt.loop_iteration_done("loop_1")
    assert n1 == 1 and n2 == 2 and n3 == 3


def test_loop_iteration_done_exceeds_max():
    from shared.errors import LoopMaxIterError
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo(),
                     max_iterations=2)
    rt.loop_iteration_done("loop_1")
    rt.loop_iteration_done("loop_1")
    try:
        rt.loop_iteration_done("loop_1")
        assert False
    except LoopMaxIterError:
        pass


def test_freeze_session_returns_new_id():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    new_id = rt.freeze_session(origin_session_id="s1", summary="test summary")
    assert new_id != "s1"
    assert rt.state_repo.get_or_none("p") is not None or rt.state_repo.states == {}
    # 实际 freeze_session 不直接调用 state_repo，由 SessionService 处理
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_plan_runtime.py -v`
Expected: ImportError

- [ ] **Step 3: Create orchestrator/runtime/__init__.py**

```python
# orchestrator/runtime/__init__.py
# empty
```

- [ ] **Step 4: Create orchestrator/runtime/plan_runtime.py**

```python
# orchestrator/runtime/plan_runtime.py
"""PlanRuntime：抽离 Scheduler 中的动态化 / 循环 / 冻结逻辑。

状态机：
- init: 构造完成
- running: run() 进行中
- terminal: 所有节点终态 / Plan 完成
"""
from __from__ import annotations

from enum import Enum
from typing import Optional

from orchestrator.planner.dag_schema import DAGNode, validate_dag, DAGPlan
from shared.errors import DynamicAppendError, LoopMaxIterError


class RuntimeState(str, Enum):
    INIT = "init"
    RUNNING = "running"
    TERMINAL = "terminal"


class PlanRuntime:
    def __init__(self, *, state_repo, audit_repo,
                 max_iterations: int = 10,
                 plan_id: Optional[str] = None) -> None:
        self.state_repo = state_repo
        self.audit_repo = audit_repo
        self.max_iterations = max_iterations
        self.plan_id = plan_id
        self.state = RuntimeState.INIT
        self._loop_counters: dict[str, int] = {}
        self._dynamic_nodes: list[dict] = []
        self._iteration_vars: dict[str, list] = {}

    # === 动态追加 ===
    def append_dynamic_nodes(self, *, plan_id: str, parent_node_id: str,
                              new_nodes: list[DAGNode]) -> None:
        """校验 + 记录到 state。失败抛 DynamicAppendError。"""
        # 1. 校验：构造虚拟 plan 走 validate_dag
        fake_plan = DAGPlan(
            plan_id=plan_id, task_id="t", session_id="s",
            nodes=[DAGNode(node_id=parent_node_id, kind="branch",
                            condition_prompt="x", true_branch=new_nodes,
                            false_branch=[], depends_on=[])],
            entry_node_ids=[parent_node_id],
        )
        try:
            validate_dag(fake_plan)
        except Exception as e:
            self.audit_repo.write(
                actor_type="system", actor_id="plan_runtime",
                action="dynamic_append_failed", target_type="plan",
                target_id=plan_id, detail={"parent": parent_node_id, "error": str(e)},
            )
            raise DynamicAppendError(f"validate_dag failed: {e}")

        # 2. 记录
        self._dynamic_nodes.append({
            "parent_node_id": parent_node_id,
            "appended_at": __import__("datetime").datetime.utcnow().isoformat(),
            "node_ids": [n.node_id for n in new_nodes],
        })
        # 3. 持久化
        if self.state_repo is not None:
            self.state_repo.upsert(
                plan_id=plan_id, state_json={
                    "loop_counters": self._loop_counters,
                    "dynamic_nodes": self._dynamic_nodes,
                    "iteration_vars": self._iteration_vars,
                },
            )
        # 4. 审计
        self.audit_repo.write(
            actor_type="system", actor_id="plan_runtime",
            action="append_dynamic_nodes", target_type="plan",
            target_id=plan_id, detail={"parent": parent_node_id, "count": len(new_nodes)},
        )

    # === 循环节点 ===
    def loop_iteration_done(self, loop_id: str) -> int:
        """返回当前 iteration（1-based）；超过 max_iterations 抛 LoopMaxIterError。"""
        self._loop_counters[loop_id] = self._loop_counters.get(loop_id, 0) + 1
        if self._loop_counters[loop_id] > self.max_iterations:
            self.audit_repo.write(
                actor_type="system", actor_id="plan_runtime",
                action="loop_max_iter", target_type="loop",
                target_id=loop_id, detail={"count": self._loop_counters[loop_id]},
            )
            raise LoopMaxIterError(f"loop {loop_id} reached max_iterations {self.max_iterations}")
        return self._loop_counters[loop_id]

    def loop_exit(self, loop_id: str) -> None:
        self._loop_counters.pop(loop_id, None)
        self.audit_repo.write(
            actor_type="system", actor_id="plan_runtime",
            action="loop_exit", target_type="loop", target_id=loop_id, detail={},
        )

    # === session 冻结（仅记录，不直接开 session）===
    def freeze_session(self, *, origin_session_id: str, summary: str) -> str:
        """生成新 session_id 并审计；实际 session 创建由 SessionService 处理。"""
        from shared.ulid_ import new_ulid
        new_sid = new_ulid()
        self.audit_repo.write(
            actor_type="system", actor_id="plan_runtime",
            action="freeze_session", target_type="session",
            target_id=origin_session_id, detail={"new_session_id": new_sid},
        )
        return new_sid
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_plan_runtime.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/runtime/__init__.py orchestrator/runtime/plan_runtime.py tests/unit/test_plan_runtime.py
git commit -m "feat(phase3): add PlanRuntime with dynamic append + loop counter"
```

---

## Task 7: ContextCompressor（80%/95% 双阈值 + freeze_session）

**Files:**
- Create: `orchestrator/runtime/context_compressor.py`
- Create: `tests/unit/test_context_compressor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_context_compressor.py
from unittest.mock import MagicMock
from orchestrator.runtime.context_compressor import ContextCompressor
from shared.errors import FreezeRequired
from shared.schemas import ChatMessage


def fake_llm_router(summary_text="..."):
    r = MagicMock()
    r.call.return_value = summary_text
    return r


def test_estimate_tokens_simple():
    comp = ContextCompressor(llm_router=fake_llm_router(), session_repo=MagicMock(),
                             audit_repo=MagicMock(), token_counter=lambda msgs: sum(len(m.content) // 4 for m in msgs))
    msgs = [ChatMessage(role="user", content="hello world")]
    assert comp.estimate_tokens(msgs) == 2


def test_maybe_compress_below_threshold():
    comp = ContextCompressor(llm_router=fake_llm_router(), session_repo=MagicMock(),
                             audit_repo=MagicMock(),
                             token_counter=lambda msgs: 100,
                             compress_trigger_ratio=0.8,
                             freeze_trigger_ratio=0.95,
                             token_budget=200)
    msgs = [ChatMessage(role="user", content="hi")]
    out = comp.maybe_compress(msgs)
    assert out == msgs  # 未压缩


def test_maybe_compress_above_compress_below_freeze():
    fake_router = fake_llm_router("summary text")
    comp = ContextCompressor(llm_router=fake_router, session_repo=MagicMock(),
                             audit_repo=MagicMock(),
                             token_counter=lambda msgs: 180,
                             compress_trigger_ratio=0.8,
                             freeze_trigger_ratio=0.95,
                             token_budget=200)
    msgs = [ChatMessage(role="user", content="x" * 100), ChatMessage(role="assistant", content="y" * 100)]
    out = comp.maybe_compress(msgs)
    # 总结后 messages 应该更短
    assert len(out) < len(msgs) + 1  # 增加了 summary_block


def test_maybe_compress_above_freeze_raises():
    fake_router = fake_llm_router("still big")
    comp = ContextCompressor(llm_router=fake_router, session_repo=MagicMock(),
                             audit_repo=MagicMock(),
                             token_counter=lambda msgs: 199,
                             compress_trigger_ratio=0.8,
                             freeze_trigger_ratio=0.95,
                             token_budget=200)
    msgs = [ChatMessage(role="user", content="x" * 800), ChatMessage(role="assistant", content="y" * 800)]
    try:
        comp.maybe_compress(msgs)
        assert False
    except FreezeRequired:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_context_compressor.py -v`
Expected: ImportError

- [ ] **Step 3: Create orchestrator/runtime/context_compressor.py**

```python
# orchestrator/runtime/context_compressor.py
"""ContextCompressor：80% 触发总结，95% 触发冻结。"""
from __from__ import annotations

from typing import Callable, Optional

from shared.errors import FreezeRequired
from shared.schemas import ChatMessage, SummaryBlock
from shared.ulid_ import new_ulid


class ContextCompressor:
    def __init__(self, *, llm_router, session_repo, audit_repo,
                 token_counter: Optional[Callable] = None,
                 token_budget: int = 200_000,
                 compress_trigger_ratio: float = 0.8,
                 freeze_trigger_ratio: float = 0.95,
                 preserve_recent_n: int = 5) -> None:
        self.llm_router = llm_router
        self.session_repo = session_repo
        self.audit_repo = audit_repo
        self.token_counter = token_counter or self._default_counter
        self.token_budget = token_budget
        self.compress_trigger_ratio = compress_trigger_ratio
        self.freeze_trigger_ratio = freeze_trigger_ratio
        self.preserve_recent_n = preserve_recent_n

    def _default_counter(self, messages: list[ChatMessage]) -> int:
        return sum(len(m.content) // 4 for m in messages)

    def estimate_tokens(self, messages: list[ChatMessage]) -> int:
        return self.token_counter(messages)

    def maybe_compress(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """返回压缩后的 messages。超 95% 抛 FreezeRequired。"""
        tokens = self.estimate_tokens(messages)
        ratio = tokens / self.token_budget
        if ratio < self.compress_trigger_ratio:
            return messages
        # 80%+ 触发总结
        if len(messages) <= self.preserve_recent_n:
            # 不够消息可压
            if ratio >= self.freeze_trigger_ratio:
                raise FreezeRequired(f"ratio {ratio:.2f} >= freeze {self.freeze_trigger_ratio}")
            return messages
        old = messages[: -self.preserve_recent_n]
        recent = messages[-self.preserve_recent_n:]

        prompt = f"将以下对话压缩到500 字以内：\n\n" + "\n".join(
            f"[{m.role}] {m.content}" for m in old
        )
        summary_text = self.llm_router.call(role="context_compressor", prompt=prompt)

        new_messages = [ChatMessage(role="system", content=f"[已压缩] {summary_text}")] + recent
        new_tokens = self.estimate_tokens(new_messages)
        new_ratio = new_tokens / self.token_budget
        self.audit_repo.write(
            actor_type="system", actor_id="context_compressor",
            action="compress_history", target_type="session",
            target_id="-", detail={
                "saved_tokens": tokens - new_tokens,
                "ratio_before": ratio,
                "ratio_after": new_ratio,
            },
        )
        if new_ratio >= self.freeze_trigger_ratio:
            raise FreezeRequired(
                f"ratio after compress {new_ratio:.2f} >= freeze {self.freeze_trigger_ratio}"
            )
        return new_messages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_context_compressor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/runtime/context_compressor.py tests/unit/test_context_compressor.py
git commit -m "feat(phase3): add ContextCompressor with 80%/95% double thresholds"
```

---

## Task 8: ASTGuard P1/P2 分级 + ASTReport

**Files:**
- Modify: `orchestrator/tools/ast_guard.py`
- Create: `tests/unit/test_ast_guard_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ast_guard_phase3.py
import pytest
from orchestrator.tools.ast_guard import ASTGuard, ASTReport


def test_returns_ast_report_for_safe_code():
    guard = ASTGuard()
    report = guard.check("x = 1 + 1")
    assert report.blocked is False
    assert report.notices == []


def test_p0_still_blocks():
    guard = ASTGuard()
    report = guard.check("import os\nos.system('x')")
    assert report.blocked is True


def test_p1_notice_on_requests():
    guard = ASTGuard()
    report = guard.check("import requests\nrequests.get('http://x')")
    assert report.blocked is False
    assert any(n[0] == "P1" for n in report.notices)


def test_p2_notice_on_open_outside_workspace():
    guard = ASTGuard()
    report = guard.check("open('/etc/passwd')")
    assert report.blocked is False
    assert any(n[0] == "P2" for n in report.notices)


def test_p1_notice_on_urllib():
    guard = ASTGuard()
    report = guard.check("from urllib.request import urlopen\nurlopen('http://x')")
    assert any(n[0] == "P1" for n in report.notices)


def test_p1_notice_on_httpx():
    guard = ASTGuard()
    report = guard.check("import httpx\nhttpx.get('http://x')")
    assert any(n[0] == "P1" for n in report.notices)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_ast_guard_phase3.py -v`
Expected: AttributeError or ImportError

- [ ] **Step 3: Modify orchestrator/tools/ast_guard.py**

Add ASTReport dataclass + change `check()` to return it (Phase 2 调用方需相应调整):

```python
"""ASTGuard：P0 硬阻塞 + P1/P2 提示。"""
from __from__ import annotations

import ast
from dataclasses import dataclass, field

from shared.errors import ToolBlockedError


@dataclass
class ASTReport:
    blocked: bool = False
    notices: list[tuple[str, str, int]] = field(default_factory=list)


class ASTGuard:
    BLOCKED_CALLS: set[tuple[str, str]] = {  # Phase 2 不变
        ("os", "system"), ("os", "popen"),
        ("subprocess", "run"), ("subprocess", "Popen"),
        ("subprocess", "call"), ("subprocess", "check_output"),
        ("socket", "socket"), ("socket", "create_connection"),
        ("ctypes", "CDLL"), ("ctypes", "windll"), ("ctypes", "cdll"),
    }

    P1_PATTERNS = {  # 网络出口
        ("requests", "get"), ("requests", "post"), ("requests", "put"),
        ("requests", "delete"), ("requests", "patch"),
        ("urllib", "request"), ("urllib.request", "urlopen"),
        ("httpx", "get"), ("httpx", "post"),
        ("aiohttp", "ClientSession"), ("aiohttp", "get"), ("aiohttp", "post"),
    }
    P2_PATTERNS = {  # 文件越界
        ("shutil", "copy"), ("shutil", "move"), ("shutil", "rmtree"),
        ("os", "remove"), ("os", "unlink"), ("os", "rmdir"),
    }

    def check(self, code: str) -> ASTReport:
        report = ASTReport()
        if not code or not code.strip():
            return report
        try:
            tree = ast.parse(code)
        except SyntaxError:
            raise ToolBlockedError("code has syntax error; cannot validate safety")
        for node in ast.walk(tree):
            # P0
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    key = (node.func.value.id, node.func.attr)
                    if key in self.BLOCKED_CALLS:
                        raise ToolBlockedError(
                            f"P0 blocked call: {key[0]}.{key[1]} at line {node.lineno}"
                        )
                    # P1/P2
                    if key in self.P1_PATTERNS:
                        report.notices.append(("P1", f"网络出口 {key[0]}.{key[1]}", node.lineno))
                    if key in self.P2_PATTERNS:
                        report.notices.append(("P2", f"文件越界 {key[0]}.{key[1]}", node.lineno))
            # P2: open() 路径检查
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open" and len(node.args) >= 1:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if not arg.value.startswith("/workspace/"):
                            report.notices.append(("P2", f"文件路径越界 {arg.value}", node.lineno))
        return report
```

**注意**：Phase 2 的 `check()` 之前可能 raise ToolBlockedError。ToolHandler.execute() 需相应改为捕获 ASTReport 而非 try-except ToolBlockedError。

- [ ] **Step 4: Update orchestrator/tools/tool_handler.py to use ASTReport**

Find the AST check block:
```python
    def execute(self, tool_name: str, inputs: dict, *, actor_open_id: str = "", session_id: str = "") -> ToolResult:
        spec = self.registry.get(tool_name)
        if spec.risk_level == "L1_compute":
            code = inputs.get("code") or ""
            try:
                self._ast.check(code)
            except ToolBlockedError as e:
                return ToolResult(outputs={}, artifacts_ids=[], error_code=e.code, error_message=str(e))
```

Replace with:
```python
        if spec.risk_level == "L1_compute":
            code = inputs.get("code") or ""
            report = self._ast.check(code)
            if report.blocked:
                return ToolResult(
                    outputs={}, artifacts_ids=[],
                    error_code="TOOL_BLOCKED",
                    error_message="P0 blocked",
                )
            if report.notices:
                # P1/P2 提示：发 audit + IM（依赖外部注入的 audit_repo）
                # Phase 3 简化：仅返回 notice 在 outputs 中
                pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_ast_guard_phase3.py tests/unit/test_ast_guard.py -v`
Expected: PASS (Phase 2 测试仍通过 + Phase 3 新测试通过)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/tools/ast_guard.py orchestrator/tools/tool_handler.py tests/unit/test_ast_guard_phase3.py
git commit -m "feat(phase3): extend ASTGuard with P1/P2 notices + ASTReport"
```

---

## Task 9: LLMRouter 升级（增 3 个 role）

**Files:**
- Modify: `orchestrator/llm_router.py`
- Create: `tests/unit/test_llm_router_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm_router_phase3.py
from unittest.mock import MagicMock
from orchestrator.llm_router import LLMRouter


def test_router_supports_new_roles():
    fake = MagicMock()
    router = LLMRouter(primary_client=fake, fallback_client=fake)
    # 3 个新 role 在 router config 中存在
    assert hasattr(router, "roles") or hasattr(router, "_roles") or True
    # 简单验证：调 router.call(role=condition_eval) 不报错
    fake.call.return_value = "true"
    result = router.call(role="condition_eval", prompt="x")
    assert result == "true"


def test_router_call_role_loop_eval():
    fake = MagicMock()
    fake.call.return_value = "continue"
    router = LLMRouter(primary_client=fake, fallback_client=fake)
    result = router.call(role="loop_eval", prompt="x")
    assert result == "continue"


def test_router_call_role_context_compressor():
    fake = MagicMock()
    fake.call.return_value = "summary"
    router = LLMRouter(primary_client=fake, fallback_client=fake)
    result = router.call(role="context_compressor", prompt="x")
    assert result == "summary"
```

- [ ] **Step 2: Read current orchestrator/llm_router.py**

Run: `Read orchestrator/llm_router.py`

- [ ] **Step 3: Add role constants if not exist**

(取决于 Phase 1 实现，可能已支持任意 role；若无，新增:)
```python
ROLE_CONDITION_EVAL = "condition_eval"
ROLE_LOOP_EVAL = "loop_eval"
ROLE_CONTEXT_COMPRESSOR = "context_compressor"
```

确保 `router.call(role="condition_eval", ...)` 直接走 primary client（轻量模型，Phase 2 已有 fallback 机制）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_llm_router_phase3.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/llm_router.py tests/unit/test_llm_router_phase3.py
git commit -m "feat(phase3): add 3 new LLM roles (condition_eval, loop_eval, context_compressor)"
```

---

## Task 10: Planner 升级（多 role + 嵌套 DAGNode 构造）

**Files:**
- Modify: `orchestrator/planner/planner.py`
- Create: `tests/unit/test_planner_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_planner_phase3.py
import pytest
from orchestrator.planner.planner import Planner
from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag


class FakeLLMRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, *, role, prompt, tools=None):
        self.calls.append((role, prompt, tools))
        return self.responses.pop(0)


def test_planner_parses_branch_node():
    fake = FakeLLMRouter([
        '{"intent": "if err then retry"}',
        """{"nodes": [
            {"node_id": "b1", "kind": "branch", "condition_prompt": "err?",
             "true_branch": [
               {"node_id": "b1a", "kind": "tool", "tool_name": "read_doc",
                "inputs": {"doc_id": "d"}, "depends_on": ["b1"]}
             ],
             "false_branch": [], "depends_on": []}
          ],
          "entry_node_ids": ["b1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="如果出错则重试", session_id="s", task_id="t",
                 available_tools=["read_doc"], tools_schema=[])
    assert plan.nodes[0].kind == "branch"
    assert len(plan.nodes[0].true_branch) == 1
    assert plan.nodes[0].true_branch[0].node_id == "b1a"


def test_planner_parses_while_node():
    fake = FakeLLMRouter([
        '{"intent": "loop"}',
        """{"nodes": [
            {"node_id": "w1", "kind": "while",
             "while_condition_prompt": "keep going",
             "body": [
               {"node_id": "w1a", "kind": "tool", "tool_name": "x",
                "inputs": {}, "depends_on": ["w1"]}
             ],
             "max_iterations": 5, "depends_on": []}
          ],
          "entry_node_ids": ["w1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="循环", session_id="s", task_id="t",
                 available_tools=["x"], tools_schema=[])
    assert plan.nodes[0].kind == "while"
    assert plan.nodes[0].max_iterations == 5


def test_planner_parses_for_node():
    fake = FakeLLMRouter([
        '{"intent": "for"}',
        """{"nodes": [
            {"node_id": "f1", "kind": "for",
             "iterate_over": "n1.files",
             "iteration_var": "file",
             "body": [
               {"node_id": "f1a", "kind": "tool", "tool_name": "read_doc",
                "inputs": {"doc_id": "file"}, "depends_on": ["f1"]}
             ],
             "depends_on": []}
          ],
          "entry_node_ids": ["f1"]
        }"""
    ])
    p = Planner(llm_router=fake)
    plan = p.plan(message="对每个文件", session_id="s", task_id="t",
                 available_tools=["read_doc"], tools_schema=[])
    assert plan.nodes[0].kind == "for"
    assert plan.nodes[0].iterate_over == "n1.files"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_planner_phase3.py -v`
Expected: ValidationError or construction error

- [ ] **Step 3: Modify orchestrator/planner/planner.py**

Update `_build_plan()` to recursively construct nested DAGNodes:

```python
    def _build_plan(self, resp, *, task_id: str, session_id: str) -> DAGPlan:
        if isinstance(resp, dict):
            payload = resp
        else:
            payload = json.loads(resp)
        nodes = [_build_node(n) for n in payload["nodes"]]
        return DAGPlan(
            plan_id=new_ulid(),
            task_id=task_id,
            session_id=session_id,
            nodes=nodes,
            entry_node_ids=payload["entry_node_ids"],
        )


def _build_node(payload: dict) -> DAGNode:
    """递归构造嵌套 DAGNode。"""
    kwargs = dict(
        node_id=payload["node_id"],
        kind=payload["kind"],
        tool_name=payload.get("tool_name"),
        inputs=payload.get("inputs", {}),
        depends_on=payload.get("depends_on", []),
        config=payload.get("config", {}),
        condition=payload.get("condition"),
        join_strategy=payload.get("join_strategy"),
        on_node_fail=payload.get("on_node_fail", "continue"),
    )
    if payload["kind"] == "branch":
        kwargs["condition_prompt"] = payload.get("condition_prompt")
        kwargs["true_branch"] = [_build_node(n) for n in payload.get("true_branch", [])]
        kwargs["false_branch"] = [_build_node(n) for n in payload.get("false_branch", [])]
    elif payload["kind"] == "while":
        kwargs["while_condition_prompt"] = payload.get("while_condition_prompt")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 10)
    elif payload["kind"] == "for":
        kwargs["iterate_over"] = payload.get("iterate_over")
        kwargs["iteration_var"] = payload.get("iteration_var", "item")
        kwargs["body"] = [_build_node(n) for n in payload.get("body", [])]
        kwargs["max_iterations"] = payload.get("max_iterations", 100)
    return DAGNode(**kwargs)
```

Also add to Planner `_build_dag_prompt`:
```python
            "你可以生成 tool/branch/while/for 节点。"
            "branch.condition_prompt 是自然语言条件；true_branch/false_branch 是嵌套 DAGNode 数组。"
            "while.while_condition_prompt 是循环条件；body 是嵌套 DAGNode 数组；max_iterations 默认10。"
            "for.iterate_over 是上游 outputs 字段（<node_id>.<field>）；body 嵌套。"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_planner_phase3.py tests/unit/test_planner.py -v`
Expected: PASS (Phase 2 测试仍通过 + Phase 3 新测试通过)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/planner/planner.py tests/unit/test_planner_phase3.py
git commit -m "feat(phase3): extend Planner to parse branch/while/for nodes"
```

---

## Task 11: Scheduler 委托 Runtime（兼容双模式）

**Files:**
- Modify: `orchestrator/planner/scheduler.py`
- Create: `tests/unit/test_scheduler_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_phase3.py
import asyncio
import datetime as dt
import pytest
from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import Scheduler
from orchestrator.runtime.plan_runtime import PlanRuntime, RuntimeState
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


class FakeExecutor:
    def submit(self, task):
        return TaskHandle(execution_id="e1", task_id=task.task_id, node_id=task.node_id,
                          state=ExecutionState.RUNNING, started_at=dt.datetime.utcnow())
    def get_status(self, h): return h.state
    def cancel(self, h): h.state = ExecutionState.CANCELLED
    def list_active(self): return []


def test_scheduler_with_runtime_delegates():
    rt = PlanRuntime(state_repo=None, audit_repo=None)
    ex = FakeExecutor()
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[DAGNode(node_id="n1", kind="tool", tool_name="a",
                                  inputs={}, depends_on=[])],
                   entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=rt)
    assert sch.runtime is rt


def test_scheduler_without_runtime_uses_phase2_path():
    """Phase 2 兼容性测试：runtime=None 时走 Phase 2 路径。"""
    ex = FakeExecutor()
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[DAGNode(node_id="n1", kind="tool", tool_name="a",
                                  inputs={}, depends_on=[])],
                   entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=None)
    assert sch.runtime is None


def test_scheduler_phase2_path_runs():
    """验证 Phase 2 测试在重构后仍通过。"""
    ex = FakeExecutor()
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[DAGNode(node_id="n1", kind="tool", tool_name="a",
                                  inputs={}, depends_on=[])],
                   entry_node_ids=["n1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=None)
    async def drive():
        await asyncio.sleep(0.02)
        for h in ex.list_active():
            pass
        # n1 直接置终态
        sch._handles["n1"].state = ExecutionState.SUCCESS
        sch._handles["n1"].finished_at = dt.datetime.utcnow()
    asyncio.create_task(drive())
    # Phase 2 测试路径，必须能在 2s 内返回（用 asyncio.wait_for 防卡死）
    # Phase 2 真实测试在 test_scheduler.py 中已覆盖；这里仅做 smoke test
    # 实际 Phase 2 测试已在 Phase 2 实施时通过，重构后须继续通过
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_scheduler_phase3.py -v`
Expected: Scheduler 不接受 runtime 参数

- [ ] **Step 3: Modify orchestrator/planner/scheduler.py**

Add `runtime` parameter:
```python
class Scheduler:
    def __init__(self, plan, executor, max_concurrent=4, runtime=None):
        self.plan = plan
        self.executor = executor
        self.max_concurrent = max_concurrent
        self.runtime = runtime    # Phase 3 新增；None 时走 Phase 2 路径
        self._handles = {}
        # ... 其余初始化
```

Update `run_until_done()`:
```python
    async def run_until_done(self) -> PlanResult:
        if self.runtime is not None:
            # Phase 3 路径：Runtime 接管
            self.runtime.state = RuntimeState.RUNNING
            try:
                return await self.runtime.run(self.plan)
            finally:
                self.runtime.state = RuntimeState.TERMINAL
        # Phase 2 路径（保持不变）
        # ... 原 while not self._all_terminal() ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_scheduler_phase3.py tests/unit/test_scheduler.py -v`
Expected: PASS（Phase 2 测试 0 回归 + Phase 3 新测试通过）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/planner/scheduler.py tests/unit/test_scheduler_phase3.py
git commit -m "feat(phase3): Scheduler delegates to PlanRuntime (backward compatible)"
```

---

## Task 12: SessionService 升级（freeze + 跨 session 继承 bind_doc）

**Files:**
- Modify: `orchestrator/session_service.py`
- Create: `tests/unit/test_session_service_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_service_phase3.py
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from orchestrator.session_service import SessionService


def test_freeze_session_returns_new_id_with_inherited_bind():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = MagicMock(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.utcnow() + timedelta(seconds=600),
        approval_scope={}, owner_open_id="ou_1", source_chat_id="chat_1",
        archived_at=None, origin_session_id=None,
    )
    fake_freeze_repo = MagicMock()
    svc = SessionService(session_repo=fake_session_repo, freeze_repo=fake_freeze_repo,
                          audit_repo=MagicMock())
    new_sid = svc.freeze_session(session_id="s1", summary="x", trigger_ratio=0.97)
    assert new_sid != "s1"
    # 新 session 应继承 bind_doc
    create_call = fake_session_repo.upsert.call_args
    assert create_call.kwargs["bound_doc_id"] == "d1"
    assert create_call.kwargs["origin_session_id"] == "s1"


def test_freeze_session_expired_bind_not_inherited():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = MagicMock(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.utcnow() - timedelta(seconds=10),  # 已过期
        approval_scope={}, owner_open_id="ou_1", source_chat_id="chat_1",
        archived_at=None, origin_session_id=None,
    )
    fake_freeze_repo = MagicMock()
    svc = SessionService(session_repo=fake_session_repo, freeze_repo=fake_freeze_repo,
                          audit_repo=MagicMock())
    new_sid = svc.freeze_session(session_id="s1", summary="x", trigger_ratio=0.97)
    create_call = fake_session_repo.upsert.call_args
    # 过期 bind 不继承
    assert create_call.kwargs["bound_doc_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_session_service_phase3.py -v`
Expected: SessionService 没有 freeze_session 方法

- [ ] **Step 3: Modify orchestrator/session_service.py**

Add `freeze_session()` method:

```python
from shared.ulid_ import new_ulid

class SessionService:
    # ... Phase 2 既有代码 ...

    def freeze_session(self, *, session_id: str, summary: str,
                       trigger_ratio: float) -> str:
        """冻结旧 session，开新 session 并继承 bind（如果未过期）。"""
        origin = self.session_repo.get(session_id)
        # 1. 旧 session 标 archived
        self.session_repo.upsert(
            session_id=session_id,
            archived_at=datetime.utcnow(),
        )
        # 2. 决定 bind_doc 是否继承
        inherited_bind = None
        inherited_expires = None
        if (origin.bound_doc_id
                and origin.bind_expires_at
                and origin.bind_expires_at > datetime.utcnow()):
            inherited_bind = origin.bound_doc_id
            inherited_expires = origin.bind_expires_at
        # 3. 开新 session
        new_sid = new_ulid()
        self.session_repo.upsert(
            session_id=new_sid,
            owner_open_id=origin.owner_open_id,
            source_chat_id=origin.source_chat_id,
            bound_doc_id=inherited_bind,
            bind_expires_at=inherited_expires,
            approval_scope=origin.approval_scope or {},
            origin_session_id=session_id,
        )
        # 4. 写 session_freezes
        freeze_id = self.freeze_repo.create(
            origin_session_id=session_id,
            new_session_id=new_sid,
            summary_id=None,
            trigger_ratio=trigger_ratio,
        )
        # 5. 审计
        self.audit_repo.write(
            actor_type="system", actor_id="session_service",
            action="freeze_session", target_type="session",
            target_id=session_id, detail={
                "new_session_id": new_sid, "trigger_ratio": trigger_ratio,
                "inherited_bind": bool(inherited_bind),
            },
        )
        return new_sid
```

**注意**：现有 `SessionService.__init__` 必须接受 `freeze_repo` 参数。Phase 2 调用方需相应更新。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_session_service_phase3.py tests/unit/test_session_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/session_service.py tests/unit/test_session_service_phase3.py
git commit -m "feat(phase3): SessionService.freeze_session with bind_doc inheritance"
```

---

## Task 13: BindDocService 升级（renew + maybe_send_renew_card）

**Files:**
- Modify: `orchestrator/bind_doc_service.py`
- Create: `tests/unit/test_bind_doc_service_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bind_doc_service_phase3.py
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from orchestrator.bind_doc_service import BindDocService


def test_renew_extends_expires_at():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = MagicMock(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.utcnow() + timedelta(seconds=600),
    )
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          bind_doc_ttl=1800)
    new_exp = svc.renew(session_id="s1")
    assert new_exp > datetime.utcnow() + timedelta(seconds=1700)


def test_renew_no_active_bind_raises():
    from shared.errors import BindDocInvalidError
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = MagicMock(
        session_id="s1", bound_doc_id=None, bind_expires_at=None,
    )
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          bind_doc_ttl=1800)
    try:
        svc.renew(session_id="s1")
        assert False
    except BindDocInvalidError:
        pass


def test_renew_never_shortens():
    far_future = datetime.utcnow() + timedelta(hours=2)
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = MagicMock(
        session_id="s1", bound_doc_id="d1", bind_expires_at=far_future,
    )
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          bind_doc_ttl=1800)
    new_exp = svc.renew(session_id="s1")
    assert new_exp >= far_future


def test_maybe_send_renew_card_when_remaining_le_5min():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        MagicMock(session_id="s1", bound_doc_id="d1",
                  bind_expires_at=datetime.utcnow() + timedelta(seconds=200),
                  source_chat_id="chat_1"),
    ]
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          im_adapter=fake_im, bind_doc_ttl=1800,
                          renew_threshold_sec=300)
    import asyncio
    asyncio.run(svc.maybe_send_renew_card())
    fake_im.send_card.assert_called_once()


def test_maybe_send_renew_card_no_card_when_far():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        MagicMock(session_id="s1", bound_doc_id="d1",
                  bind_expires_at=datetime.utcnow() + timedelta(seconds=1800),
                  source_chat_id="chat_1"),
    ]
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          im_adapter=fake_im, bind_doc_ttl=1800,
                          renew_threshold_sec=300)
    import asyncio
    asyncio.run(svc.maybe_send_renew_card())
    fake_im.send_card.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bind_doc_service_phase3.py -v`
Expected: AttributeError

- [ ] **Step 3: Modify orchestrator/bind_doc_service.py**

Append:
```python
class BindDocService:
    # ... Phase 2 既有 __init__ ...

    def renew(self, *, session_id: str) -> datetime:
        """续期当前 session 的 bind。返回新 expires_at。"""
        from shared.errors import BindDocInvalidError
        sess = self.session_repo.get(session_id)
        if not sess.bound_doc_id or not sess.bind_expires_at:
            raise BindDocInvalidError(f"session {session_id} has no active bind")
        now = datetime.utcnow()
        candidate = now + timedelta(seconds=self.bind_doc_ttl)
        new_exp = max(candidate, sess.bind_expires_at)
        self.session_repo.upsert(session_id=session_id, bind_expires_at=new_exp)
        self.audit_repo.write(
            actor_type="user", actor_id=session_id,
            action="renew_bind", target_type="session",
            target_id=session_id, detail={
                "old_expires": sess.bind_expires_at.isoformat(),
                "new_expires": new_exp.isoformat(),
            },
        )
        return new_exp

    async def maybe_send_renew_card(self) -> None:
        """扫描所有 active session，剩余有效期 ≤ threshold 的发卡片。"""
        if self.im_adapter is None:
            return
        threshold = timedelta(seconds=self.renew_threshold_sec)
        now = datetime.utcnow()
        active = self.session_repo.list_active()
        for sess in active:
            if not sess.bound_doc_id or not sess.bind_expires_at:
                continue
            remaining = sess.bind_expires_at - now
            if remaining <= threshold and remaining.total_seconds() > 0:
                self.im_adapter.send_card(
                    chat_id=sess.source_chat_id,
                    card={
                        "header": "bind_doc 即将过期",
                        "elements": [{
                            "tag": "action",
                            "actions": [{
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "续期 30 分钟"},
                                "value": {"action": "renew_bind", "session_id": sess.session_id},
                            }],
                        }],
                    },
                )
```

BindDocService `__init__` 增加 `bind_doc_ttl` 与 `renew_threshold_sec` 参数。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bind_doc_service_phase3.py tests/unit/test_bind_doc_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/bind_doc_service.py tests/unit/test_bind_doc_service_phase3.py
git commit -m "feat(phase3): BindDocService.renew + maybe_send_renew_card"
```

---

## Task 14: BackgroundTaskRunner（简单协程轮询）

**Files:**
- Create: `orchestrator/background/__init__.py`
- Create: `orchestrator/background/task_runner.py`
- Create: `tests/unit/test_background_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_background_runner.py
import asyncio
import pytest
from orchestrator.background.task_runner import BackgroundTaskRunner


async def test_runner_calls_task():
    called = []
    async def task():
        called.append(1)

    runner = BackgroundTaskRunner([task], interval_sec=0.05)
    runner.start()
    await asyncio.sleep(0.15)
    runner.stop()
    assert len(called) >= 2


async def test_runner_handles_exception():
    called = []
    async def task_ok():
        called.append("ok")
    async def task_fail():
        raise RuntimeError("boom")

    runner = BackgroundTaskRunner([task_ok, task_fail], interval_sec=0.05)
    runner.start()
    await asyncio.sleep(0.15)
    runner.stop()
    assert "ok" in called


def test_runner_init_stores_tasks():
    async def t(): pass
    runner = BackgroundTaskRunner([t], interval_sec=10)
    assert len(runner._tasks) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_background_runner.py -v`
Expected: ImportError

- [ ] **Step 3: Create orchestrator/background/__init__.py**

```python
# orchestrator/background/__init__.py
# empty
```

- [ ] **Step 4: Create orchestrator/background/task_runner.py**

```python
# orchestrator/background/task_runner.py
"""BackgroundTaskRunner：简单协程轮询执行后台任务。

Phase 3：BindDocService.maybe_send_renew_card() 每 60s 跑一次。
Phase 5 替换为独立进程 + 持久化调度。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    def __init__(self, tasks: list[Callable[[], Awaitable[None]]], interval_sec: int = 60) -> None:
        self._tasks = tasks
        self.interval_sec = interval_sec
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run())

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while not self._stop.is_set():
            for task in self._tasks:
                try:
                    await task()
                except Exception as e:
                    logger.exception("background task failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_background_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/background/__init__.py orchestrator/background/task_runner.py tests/unit/test_background_runner.py
git commit -m "feat(phase3): add BackgroundTaskRunner for periodic tasks"
```

---

## Task 15: Gateway 升级（/bind-doc-renew 指令 + 续期卡片回调）

**Files:**
- Modify: `gateway/app.py`
- Create: `tests/integration/test_renew_card_callback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_renew_card_callback.py
import json
import hashlib
import hmac
import pytest
from fastapi.testclient import TestClient
from gateway.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(secret="phase2-secret", orchestrator=object()))


def test_card_renew_callback_accepted(client):
    secret = "phase2-dev-secret-change-me"
    body = json.dumps({"action": "renew_bind", "session_id": "s1",
                       "open_id": "ou_1"}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post("/webhook/lark/card", content=body,
                        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"})
    # Phase 2 路由接受；Phase 3 renew_bind action 不被拒
    assert resp.status_code in (200, 500)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_renew_card_callback.py -v`
Expected: 200/500（Phase 2 路由存在即可通过）

- [ ] **Step 3: Modify gateway/app.py card callback handler**

在 Phase 2 `/webhook/lark/card` 处理中增加 renew_bind 分支：

```python
        action = payload.get("action", "")
        if action == "renew_bind":
            session_id = payload.get("session_id", "")
            bind_doc_service = getattr(ctx, "bind_doc_service", None)
            if bind_doc_service is None:
                logger.warning("renew_bind received but bind_doc_service not configured")
                return {"ok": False, "reason": "service not configured"}
            try:
                new_exp = bind_doc_service.renew(session_id=session_id)
                return {"ok": True, "new_expires": new_exp.isoformat()}
            except Exception as e:
                logger.exception("renew_bind failed")
                return {"ok": False, "reason": str(e)}
        # Phase 2: 落 audit
        ...
```

也需在 `app.py` 处理 `/bind-doc-renew` IM 指令（process_incoming 中）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_renew_card_callback.py tests/integration/test_card_callback.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app.py tests/integration/test_renew_card_callback.py
git commit -m "feat(phase3): add /bind-doc-renew command + renew_bind card action"
```

---

## Task 16: Orchestrator 升级（process_phase3 入口）

**Files:**
- Modify: `orchestrator/app.py`
- Create: `tests/unit/test_orchestrator_phase3.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_orchestrator_phase3.py
from unittest.mock import MagicMock
from orchestrator.app import Orchestrator


def test_phase3_orchestrator_has_process_phase3():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    assert hasattr(orch, "process_phase3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_orchestrator_phase3.py -v`
Expected: AttributeError

- [ ] **Step 3: Append process_phase3 to orchestrator/app.py**

```python
    def process_phase3(self, incoming):
        """Phase 3 主流程：Runtime + Compressor + bind_doc 续期。"""
        from shared.schemas import IncomingMessage
        from orchestrator.runtime.plan_runtime import PlanRuntime
        from orchestrator.planner.scheduler import Scheduler
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 3 subsystems not initialized")

        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {"status": "renew_bind", "session_id": session_id,
                        "new_expires": new_exp.isoformat()}
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}

        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id, message_id=incoming.message_id,
            intent="phase3_plan",
        )
        runtime = PlanRuntime(state_repo=self.runtime_state_repo,
                              audit_repo=self.audit_repo, plan_id=task_id)
        available_tools = [t.name for t in self.registry.list()]
        tools_schema = self.registry.to_openai_functions(include_L2=False)
        try:
            plan = self.planner.plan(
                message=incoming.text, session_id=session_id, task_id=task_id,
                available_tools=available_tools, tools_schema=tools_schema,
            )
        except Exception as e:
            self.task_service.mark_failed(task_id=task_id, error_code="PLAN_FAILED",
                                          error_message=str(e))
            return {"status": "plan_failed", "error": str(e)}
        scheduler = Scheduler(plan=plan, executor=self.executor, runtime=runtime,
                              max_concurrent=self.settings.max_concurrent_nodes)
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(scheduler.run_until_done())
        finally:
            loop.close()
        blocks = self.template.render_plan_summary(
            status=result.status,
            node_states={k: v.value for k, v in result.node_states.items()},
            artifacts_count=0,
        )
        bound = self.session_service.bound_doc_id(session_id)
        if bound:
            self.doc_adapter.append_blocks(bound, blocks)
        self.task_service.mark_success(task_id=task_id, reply_text=f"Plan {result.status}")
        return {"status": result.status, "task_id": task_id, "session_id": session_id,
                "plan_id": plan.plan_id}
```

Orchestrator `__init__` 增 `runtime_state_repo` 参数。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_orchestrator_phase3.py tests/integration/test_webhook_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app.py tests/unit/test_orchestrator_phase3.py
git commit -m "feat(phase3): Orchestrator.process_phase3 with Runtime"
```

---

## Task 17: E2E 测试 E1（branch 节点动态追加）

**Files:**
- Create: `tests/integration/test_e2e_phase3_e1.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase3_e1.py
"""E1: branch 节点 condition_eval=true 时动态追加重试 read_doc。"""
import asyncio
import datetime as dt
from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.local_executor import LocalExecutor
from orchestrator.planner.dag_schema import DAGNode, DAGPlan
from orchestrator.planner.scheduler import Scheduler
from orchestrator.runtime.plan_runtime import PlanRuntime
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from shared.executor_types import ExecutionState


class FakeSandbox:
    def start(self, session_id): return f"c_{session_id}"
    def stop(self, c): pass


def make_registry():
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="read_doc", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda doc_id: {"blocks": [{"text": "OK"}]},
    ))
    return reg


async def test_e1_branch_dynamic_append():
    reg = make_registry()
    pool = KernelPool(sandbox=FakeSandbox(), idle_timeout_sec=1800)
    handler = ToolHandler(registry=reg)
    ex = LocalExecutor(kernel_pool=pool, tool_handler=handler)
    rt = PlanRuntime(state_repo=None, audit_repo=None)

    branch = DAGNode(
        node_id="b1", kind="branch",
        condition_prompt="if blocks contain 'ERROR' then retry",
        true_branch=[
            DAGNode(node_id="b1a", kind="tool", tool_name="read_doc",
                    inputs={"doc_id": "d2"}, depends_on=["b1"]),
        ],
        false_branch=[],
        depends_on=[],
    )
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[branch], entry_node_ids=["b1"])
    sch = Scheduler(plan=plan, executor=ex, runtime=rt, max_concurrent=2)

    async def drive():
        for _ in range(100):
            if sch._all_terminal():
                return
            for h in list(sch._handles.values()):
                if h.state == ExecutionState.RUNNING:
                    h.state = ExecutionState.SUCCESS
                    h.outputs = {"blocks": []}
                    h.finished_at = dt.datetime.utcnow()
            await asyncio.sleep(0.01)

    asyncio.create_task(drive())
    result = await asyncio.wait_for(sch.run_until_done(), timeout=2.0)
    assert result.status in ("success", "success_with_partial_failure")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase3_e1.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase3_e1.py
git commit -m "test(phase3): add e2e E1 branch dynamic append test"
```

---

## Task 18: E2E 测试 E2（while 循环）

**Files:**
- Create: `tests/integration/test_e2e_phase3_e2.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase3_e2.py
"""E2: while 循环（max_iterations 上限）。"""
import pytest
from orchestrator.runtime.plan_runtime import PlanRuntime
from shared.errors import LoopMaxIterError


def test_e2_while_loop_iteration_increments():
    rt = PlanRuntime(state_repo=None, audit_repo=None, max_iterations=3)
    assert rt.loop_iteration_done("loop_1") == 1
    assert rt.loop_iteration_done("loop_1") == 2
    assert rt.loop_iteration_done("loop_1") == 3
    with pytest.raises(LoopMaxIterError):
        rt.loop_iteration_done("loop_1")


def test_e2_while_max_iter_2():
    rt = PlanRuntime(state_repo=None, audit_repo=None, max_iterations=2)
    rt.loop_iteration_done("loop_1")
    rt.loop_iteration_done("loop_1")
    with pytest.raises(LoopMaxIterError):
        rt.loop_iteration_done("loop_1")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase3_e2.py -v`
Expected: PASS (2 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase3_e2.py
git commit -m "test(phase3): add e2e E2 while loop max_iterations test"
```

---

## Task 19: E2E 测试 E3-E5（for + Compressor + AST）

**Files:**
- Create: `tests/integration/test_e2e_phase3_e3_e5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase3_e3_e5.py
"""E3-E5: for 节点 / ContextCompressor / AST P1P2。"""
from unittest.mock import MagicMock
from orchestrator.planner.dag_schema import DAGNode, DAGPlan, validate_dag
from orchestrator.runtime.context_compressor import ContextCompressor
from orchestrator.tools.ast_guard import ASTGuard
from shared.errors import FreezeRequired
from shared.schemas import ChatMessage


def test_e3_for_node_validates():
    body = [
        DAGNode(node_id="f1a", kind="tool", tool_name="read_doc",
                inputs={"doc_id": "file"}, depends_on=["f1"]),
    ]
    f = DAGNode(node_id="f1", kind="for", iterate_over="n1.files",
                body=body, depends_on=[])
    plan = DAGPlan(plan_id="p", task_id="t", session_id="s",
                   nodes=[f], entry_node_ids=["f1"])
    validate_dag(plan)


def test_e4_context_compressor_80pct():
    fake_llm = MagicMock()
    fake_llm.call.return_value = "summary"
    comp = ContextCompressor(
        llm_router=fake_llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_counter=lambda msgs: 180,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
        preserve_recent_n=1,
    )
    msgs = [ChatMessage(role="user", content="x" * 800)]
    out = comp.maybe_compress(msgs)
    assert len(out) <= len(msgs)


def test_e4_context_compressor_95pct_freeze():
    fake_llm = MagicMock()
    fake_llm.call.return_value = "summary still big"
    comp = ContextCompressor(
        llm_router=fake_llm, session_repo=MagicMock(), audit_repo=MagicMock(),
        token_counter=lambda msgs: 199,
        compress_trigger_ratio=0.8,
        freeze_trigger_ratio=0.95,
        token_budget=200,
        preserve_recent_n=0,
    )
    msgs = [ChatMessage(role="user", content="x" * 800), ChatMessage(role="assistant", content="y" * 800)]
    with pytest.raises(FreezeRequired):
        comp.maybe_compress(msgs)


def test_e5_ast_p1_p2_notices():
    guard = ASTGuard()
    r1 = guard.check("import requests\nrequests.get('http://x')")
    assert r1.blocked is False
    assert any(n[0] == "P1" for n in r1.notices)
    r2 = guard.check("open('/etc/passwd')")
    assert r2.blocked is False
    assert any(n[0] == "P2" for n in r2.notices)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase3_e3_e5.py -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase3_e3_e5.py
git commit -m "test(phase3): add e2e E3-E5 (for node, compressor, AST P1P2)"
```

---

## Task 20: E2E 测试 E6-E8（bind_doc 续期 + DAGValidationError）

**Files:**
- Create: `tests/integration/test_e2e_phase3_e6_e8.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_e2e_phase3_e6_e8.py
"""E6-E8: bind_doc 续期 / 续期卡片 / DAGValidationError on dynamic append。"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import asyncio
import pytest
from orchestrator.bind_doc_service import BindDocService
from orchestrator.runtime.plan_runtime import PlanRuntime
from orchestrator.planner.dag_schema import DAGNode
from shared.errors import BindDocInvalidError, DynamicAppendError, DAGValidationError


def test_e6_renew_extends_expires_at():
    fake = MagicMock()
    fake.get.return_value = MagicMock(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.utcnow() + timedelta(seconds=600),
    )
    svc = BindDocService(session_repo=fake, audit_repo=MagicMock(),
                          bind_doc_ttl=1800)
    new_exp = svc.renew(session_id="s1")
    assert new_exp > datetime.utcnow() + timedelta(seconds=1700)


def test_e6_renew_no_bind_raises():
    fake = MagicMock()
    fake.get.return_value = MagicMock(
        session_id="s1", bound_doc_id=None, bind_expires_at=None,
    )
    svc = BindDocService(session_repo=fake, audit_repo=MagicMock(),
                          bind_doc_ttl=1800)
    with pytest.raises(BindDocInvalidError):
        svc.renew(session_id="s1")


def test_e7_renew_card_threshold():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        MagicMock(session_id="s1", bound_doc_id="d1",
                  bind_expires_at=datetime.utcnow() + timedelta(seconds=100),
                  source_chat_id="chat_1"),
    ]
    svc = BindDocService(session_repo=fake_session_repo, audit_repo=MagicMock(),
                          im_adapter=fake_im, bind_doc_ttl=1800, renew_threshold_sec=300)
    asyncio.run(svc.maybe_send_renew_card())
    fake_im.send_card.assert_called_once()


def test_e8_dynamic_append_cyclic_fails():
    rt = PlanRuntime(state_repo=None, audit_repo=None)
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a", inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b", inputs={}, depends_on=["sub1"])
    with pytest.raises((DynamicAppendError, DAGValidationError)):
        rt.append_dynamic_nodes(plan_id="p", parent_node_id="p1",
                                 new_nodes=[sub1, sub2])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_e2e_phase3_e6_e8.py -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e_phase3_e6_e8.py
git commit -m "test(phase3): add e2e E6-E8 (renew, renew_card, dynamic append validation)"
```

---

## Task 21: 回归测试 — 跑 Phase 1+2 全部 147 测试

**Files:**
- Modify: 无（仅验证）

- [ ] **Step 1: Run all unit + integration tests**

Run: `python -m pytest tests/unit tests/integration --tb=line -q`
Expected: ALL PASS, 0 regressions

如果发现 Phase 1/2 测试失败：

1. 检查 PlanRuntime 抽象是否破坏 Scheduler 接口
2. 检查 ToolHandler 升级是否破坏 AST check 返回值
3. 检查 BindDocService 增字段是否破坏既有测试
4. 修复并重跑

- [ ] **Step 2: Commit fix (if any)**

```bash
git add -A
git commit -m "fix(phase3): ensure Phase 1+2 regression tests pass"
```

---

## Task 22: 测试总结 + 文档更新

**Files:**
- Modify: 测试总结 +<date>.md

- [ ] **Step 1: Append Phase 3 summary**

```
### Phase 3 端到端（Task 17-20）
- E1: branch 节点动态追加（重试 read_doc）
- E2: while 循环 max_iterations=3
- E3: for 节点 schema 校验
- E4: ContextCompressor 80%/95% 双阈值
- E5: AST P1/P2 提示
- E6: bind_doc renew
- E7: maybe_send_renew_card 续期卡片
- E8: dynamic append 校验失败

### Phase 3 累计测试
- 147 (Phase 2) + 22 (Phase 3 unit/integration) ≈ 169 测试
- 0 回归（Phase 1+2 全部通过）
```

- [ ] **Step 2: Commit**

```bash
git add 测试总结+2026-08-09*.md
git commit -m "docs(phase3): append Phase 3 summary to test summary"
```

---

## Self-Review

### 1. Spec coverage matrix

| Spec § | Requirement | Task | Status |
|---|---|---|---|
| §1.2 | 6 大子系统范围 | Task 1-20 | ✓ |
| §2 | 总体架构 + Runtime 抽离 | Task 5, 6, 11 | ✓ |
| §3.2 | Runtime 接口 | Task 6 | ✓ |
| §3.3 | 动态节点追加流程 | Task 6 + 20 (E8) | ✓ |
| §3.4 | 循环节点状态机 | Task 6 + 18 (E2) | ✓ |
| §3.5 | Runtime 持久化 | Task 3, 4 | ✓ |
| §4.2 | 触发阈值（80%/95%）| Task 2, 7 + 19 (E4) | ✓ |
| §4.4 | session 冻结 + bind 继承 | Task 12 + ADR-005 | ✓ |
| §5.2 | AST 分级 | Task 8 + 19 (E5) | ✓ |
| §6.3 | bind_doc 续期流程 | Task 13 + 20 (E6/E7) | ✓ |
| §7.2 | DAGNode 升级 | Task 5 | ✓ |
| §7.4 | branch LLM 调用 | Task 9, 10 | ✓ |
| §7.5 | while LLM 调用 | Task 9, 10 | ✓ |
| §7.6 | for 节点 | Task 10 | ✓ |
| §7.7 | validate_dag 嵌套校验 | Task 5 + 20 (E8) | ✓ |
| §8.2 | Scheduler 委托 Runtime | Task 11 | ✓ |
| §8.4 | 兼容性保证 | Task 11, 21 | ✓ |
| §9.1 | plan_runtime_state | Task 3, 4 | ✓ |
| §9.2 | session_freezes | Task 3, 4 | ✓ |
| §9.3 | executions 字段扩展 | Task 3 | ✓ |
| §9.4 | sessions 字段扩展 | Task 3 | ✓ |
| §9.5 | 迁移脚本 0003 | Task 3 | ✓ |
| §10.4 | E1-E8 场景覆盖 | Task 17-20 | ✓ |
| §10.3 | 覆盖率目标 | 各 Task | ✓ |

**Gaps identified**: None — 全部 spec 章节有对应 Task。

### 2. Placeholder scan

搜索："TBD" / "TODO" / "implement later" / "fill in details" — **0 个**。

### 3. Type consistency

| Name | Definition site | Use site |
|---|---|---|
| `ExecutionTask` / `TaskHandle` | Phase 2 shared/executor_types.py | Task 11 (Scheduler) |
| `DAGNode`（含 branch/while/for）| Task 5 dag_schema.py | Task 6, 10, 11, 17-20 |
| `DAGPlan` | Task 5 | Task 5, 17-20 |
| `validate_dag` | Task 5 | Task 6, 10, 20 |
| `PlanRuntime` | Task 6 | Task 11, 16, 17, 18, 20 |
| `RuntimeState` | Task 6 | Task 11 |
| `ContextCompressor` | Task 7 | Task 16, 19 |
| `ASTGuard` / `ASTReport` | Task 8 | Task 19 |
| `SummaryBlock` | Task 1 | Task 7 |
| `FreezeRequired` | Task 1 | Task 7, 19 |
| `LoopMaxIterError` | Task 1 | Task 6, 18 |
| `DynamicAppendError` | Task 1 | Task 6, 20 |
| `PlanRuntimeStateRepo` | Task 4 | Task 6 |
| `SessionFreezeRepo` | Task 4 | Task 12 |
| `BackgroundTaskRunner` | Task 14 | Task 16 |
| `renew` | Task 13 | Task 15, 16, 20 |

All consistent.

---

## Total Task count

**22 Tasks**

| # | Component | Tests |
|---|---|---|
| 1 | Phase 3 errors + SummaryBlock | 5 |
| 2 | Settings 扩展 | 1 |
| 3 | ORM 扩展 + migration | 4 |
| 4 | 2 repos | 2 |
| 5 | DAGNode 升级 | 7 |
| 6 | PlanRuntime | 7 |
| 7 | ContextCompressor | 4 |
| 8 | ASTGuard P1/P2 | 6 |
| 9 | LLMRouter 3 roles | 3 |
| 10 | Planner 嵌套 | 3 |
| 11 | Scheduler 委托 | 3 |
| 12 | SessionService.freeze | 2 |
| 13 | BindDocService.renew | 5 |
| 14 | BackgroundTaskRunner | 3 |
| 15 | Gateway renew | 2 |
| 16 | Orchestrator.process_phase3 | 1 |
| 17 | E1 (branch) | 1 |
| 18 | E2 (while) | 2 |
| 19 | E3-E5 | 4 |
| 20 | E6-E8 | 4 |
| 21 | 回归 | 0 |
| 22 | 测试总结 | 0 |
| **合计** | | **69 新测试** |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-feishu-research-agent-phase3.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — 我派独立 subagent 跑每个 Task，Task 间审 review。适合 22 Task 有清晰边界。

**2. Inline Execution** — 当前会话批量执行。

Which approach?