# Feishu Research Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通一条安全、可审计的最短链路：用户在飞书 IM 发消息，系统返回 LLM 回复；若用户已通过 `/bind-doc` 绑定文档，则在授权窗口内把纯文本结果追加到该文档。

**Architecture:** 单进程 FastAPI（Gateway + Orchestrator 合并部署）；PostgreSQL 是唯一主存，飞书 Base 只做异步状态投影；文档写入只允许命中 `/bind-doc` 授权窗口的纯文本追加；所有任务级幂等靠 `message_id`。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, psycopg2-binary, httpx, pydantic v2, pytest, respx, lark-cli（外部命令），OpenAI 兼容 API。

**Reference Spec:** `docs/superpowers/specs/2026-08-08-feishu-research-agent-design.md`

---

## Phase 1 交付边界

### 要交付

1. 用户在飞书私聊里发送自然语言消息
3. 用户可通过 `/bind-doc <doc_id>` 绑定目标文档
4. 在绑定有效期内（30 分钟），系统把纯文本回复追加到该文档末尾
5. 用户可以看到任务状态和审计摘要

### 明确不做

- 不做任意 Python / R 代码执行
- 不做 Docker 沙箱
- 不做 Jupyter Kernel 恢复
- 不做多步 DAG
- 不做群聊多人协同写同一文档
- 不做图片 / 表格 / Drive 文件产物回写
- 不做完整的 IM 卡片审批流（Phase 1 用 bind-doc 前置授权代替）

---

## 关键设计约束

### 单一事实源

所有以下实体都以 PostgreSQL 为准：

- `sessions`
- `tasks`
- `doc_writes`
- `audit_logs`
- `idempotency_keys`（webhook 去重）

飞书 Base 仅做投影，投影失败不能影响主任务成功与否。

### 文档写入边界

Phase 1 只允许一种自动写文档场景：

- 当前用户已执行 `/bind-doc <doc_id>`
- 当前消息来自同一用户
- 会话还在授权窗口内（默认 30 分钟）
- 写入类型为纯文本追加

任何不满足上述条件的请求：只回 IM，不写 Doc；或提示用户先绑定文档。

### 幂等要求

必须防止以下重复副作用：

- 飞书重复投递同一条 webhook
- 网关超时后飞书重试
- 文档追加时重复写入同一条回复

幂等键：`app_id:chat_id:message_id`，同时写入 `tasks.message_id`，确保任务级幂等。

---

## 目录结构

```
feishu-research-agent/
├── pyproject.toml
├── .env.example
├── README.md
├── alembic.ini                    # DB migration
├── migrations/                    # Alembic 迁移脚本
│
├── config/
│   ├── feishu.yaml
│   ├── llm.yaml
│   └── settings.py                # 从 yaml + 环境变量构造 Settings
│
├── shared/
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic 数据模型
│   ├── enums.py                   # TaskStatus / DocWriteStatus / RiskLevel
│   ├── errors.py
│   └── ulid_.py
│
├── persistence/
│   ├── __init__.py
│   ├── engine.py                  # SQLAlchemy engine + session factory
│   ├── models.py                  # ORM 模型
│   └── repositories/
│       ├── __init__.py
│       ├── session_repo.py
│       ├── task_repo.py
│       ├── doc_write_repo.py
│       ├── audit_repo.py
│       └── idempotency_repo.py
│
├── feishu_adapter/
│   ├── __init__.py
│   ├── client.py                  # lark-cli subprocess 封装
│   ├── im_adapter.py
│   ├── doc_adapter.py
│   └── base_projection_adapter.py
│
├── gateway/
│   ├── __init__.py
│   ├── app.py                     # FastAPI 入口
│   ├── signature.py
│   ├── rate_limit.py
│   ├── idempotency.py
│   └── normalizer.py
│
├── orchestrator/
│   ├── __init__.py
│   ├── app.py                     # Orchestrator.process()
│   ├── session_service.py
│   ├── task_service.py
│   ├── bind_doc_service.py
│   ├── llm_router.py
│   └── doc_write_service.py
│
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_ulid_.py
    │   ├── test_signature.py
    │   ├── test_rate_limit.py
    │   ├── test_idempotency.py
    │   ├── test_normalizer.py
    │   ├── test_llm_router.py
    │   ├── test_session_repo.py
    │   ├── test_task_repo.py
    │   ├── test_doc_write_repo.py
    │   ├── test_audit_repo.py
    │   ├── test_bind_doc_service.py
    │   └── test_adapters.py
    └── integration/
        ├── __init__.py
        ├── test_bind_doc_flow.py
        ├── test_message_flow.py
        └── test_webhook_app.py
```

---

## Task 1: 初始化工程骨架与依赖

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `README.md`
- Create: `config/__init__.py`
- Create: `config/feishu.yaml`
- Create: `config/llm.yaml`
- Create: `config/settings.py`
- Create: `shared/__init__.py`

> **说明**：`alembic.ini` 的创建推迟到 Task 3（与首次 migration 一起）；`requires-python` 根据实际 Python 环境调整。

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "feishu-research-agent"
version = "0.1.0"
description = "Phase 1 demo: IM -> LLM -> Doc (bind-doc flow)"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn>=0.27.0",
    "httpx>=0.27.0",
    "pydantic>=2.5.0",
    "pyyaml>=6.0.1",
    "python-ulid>=2.2.0",
    "sqlalchemy>=2.0.25",
    "psycopg[binary]>=3.1.0",
    "alembic>=1.13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "respx>=0.21.0",
    "pytest-postgresql>=6.0.0",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["shared*", "gateway*", "orchestrator*", "feishu_adapter*", "persistence*", "config*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 .env.example**

```bash
# Feishu 应用凭据
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_WEBHOOK_SECRET=test_secret

# 飞书 Demo 资产
FEISHU_DEMO_DOC_ID=doccnxxxxxxxxxxxx
FEISHU_DEMO_BASE_APP_TOKEN=bascnxxxxxxxxxxxx

# LLM
LLM_PRIMARY_BASE_URL=https://api.deepseek.com/v1
LLM_PRIMARY_API_KEY=sk-xxxxxxxx
LLM_PRIMARY_MODEL=deepseek-chat

LLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FALLBACK_API_KEY=sk-xxxxxxxx
LLM_FALLBACK_MODEL=qwen-plus

# PostgreSQL
DATABASE_URL=postgresql://agent:agent@localhost:5432/agent

# 业务参数
BIND_DOC_TTL_SEC=1800
GATEWAY_RATE_LIMIT_PER_MIN=60
```

- [ ] **Step 3: 创建 config/feishu.yaml**

```yaml
app_id_env: FEISHU_APP_ID
app_secret_env: FEISHU_APP_SECRET
webhook_secret_env: FEISHU_WEBHOOK_SECRET
```

- [ ] **Step 4: 创建 config/llm.yaml**

```yaml
router:
  primary:
    base_url_env: LLM_PRIMARY_BASE_URL
    api_key_env: LLM_PRIMARY_API_KEY
    model_env: LLM_PRIMARY_MODEL
    timeout_sec: 30
  fallback:
    base_url_env: LLM_FALLBACK_BASE_URL
    api_key_env: LLM_FALLBACK_API_KEY
    model_env: LLM_FALLBACK_MODEL
    timeout_sec: 30
  max_retries: 1
```

- [ ] **Step 5: 实现 config/settings.py**

```python
"""从 yaml + 环境变量构造 Settings 单例。"""
import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    webhook_secret: str


@dataclass(frozen=True)
class LLMSettings:
    primary_base_url: str
    primary_api_key: str
    primary_model: str
    fallback_base_url: str
    fallback_api_key: str
    fallback_model: str
    max_retries: int


@dataclass(frozen=True)
class Settings:
    feishu: FeishuSettings
    llm: LLMSettings
    database_url: str
    bind_doc_ttl_sec: int
    rate_limit_per_min: int


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_settings() -> Settings:
    feishu_cfg = _load_yaml("config/feishu.yaml")
    llm_cfg = _load_yaml("config/llm.yaml")
    return Settings(
        feishu=FeishuSettings(
            app_id=os.environ[feishu_cfg["app_id_env"]],
            app_secret=os.environ[feishu_cfg["app_secret_env"]],
            webhook_secret=os.environ[feishu_cfg["webhook_secret_env"]],
        ),
        llm=LLMSettings(
            primary_base_url=os.environ[llm_cfg["router"]["primary"]["base_url_env"]],
            primary_api_key=os.environ[llm_cfg["router"]["primary"]["api_key_env"]],
            primary_model=os.environ[llm_cfg["router"]["primary"]["model_env"]],
            fallback_base_url=os.environ[llm_cfg["router"]["fallback"]["base_url_env"]],
            fallback_api_key=os.environ[llm_cfg["router"]["fallback"]["api_key_env"]],
            fallback_model=os.environ[llm_cfg["router"]["fallback"]["model_env"]],
            max_retries=llm_cfg["router"]["max_retries"],
        ),
        database_url=os.environ["DATABASE_URL"],
        bind_doc_ttl_sec=int(os.environ.get("BIND_DOC_TTL_SEC", "1800")),
        rate_limit_per_min=int(os.environ.get("GATEWAY_RATE_LIMIT_PER_MIN", "60")),
    )
```

- [ ] **Step 6: 创建 README.md**

````markdown
# Feishu Research Agent — Phase 1

## 启动

```bash
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env

# 初始化数据库
alembic upgrade head

uvicorn gateway.app:create_app --factory --host 0.0.0.0 --port 8000
```

## 测试

```bash
pytest tests/ -v
```
````

- [ ] **Step 7: 创建 shared/__init__.py**

```python
# shared package
```

- [ ] **Step 8: 安装依赖**

Run:
```bash
cd i:\飞书agent && pip install -e ".[dev]"
```
Expected: Successfully installed feishu-research-agent-0.1.0

- [ ] **Step 9: 提交**

```bash
cd i:\飞书agent
git add pyproject.toml .env.example .gitignore README.md config/ shared/__init__.py
git commit -m "chore: phase1 scaffold with deps, settings, config templates"
```

---

## Task 2: 共享枚举、错误与 ULID

**Files:**
- Create: `shared/enums.py`
- Create: `shared/errors.py`
- Create: `shared/ulid_.py`
- Create: `shared/schemas.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_ulid_.py`

- [ ] **Step 1: 创建 tests/__init__.py 与 tests/unit/__init__.py（空文件）**

- [ ] **Step 2: 写失败测试 — test_ulid_.py**

```python
"""ULID 生成与解析。"""
from shared.ulid_ import new_ulid, parse_ulid_timestamp


def test_new_ulid_is_string():
    uid = new_ulid()
    assert isinstance(uid, str)
    assert len(uid) == 26


def test_new_ulids_are_unique():
    ids = {new_ulid() for _ in range(1000)}
    assert len(ids) == 1000


def test_parse_ulid_timestamp_roundtrip():
    uid = new_ulid()
    ts = parse_ulid_timestamp(uid)
    assert ts > 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd i:\飞书agent && pytest tests/unit/test_ulid_.py -v`
Expected: ModuleNotFoundError: No module named 'shared.ulid_'

- [ ] **Step 4: 实现 shared/ulid_.py**

```python
"""ULID 封装，文件用下划线后缀避免与 python-ulid 包冲突。"""
from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


def parse_ulid_timestamp(ulid_str: str) -> int:
    return ULID.from_str(ulid_str).timestamp().int
```

- [ ] **Step 5: 实现 shared/errors.py**

```python
"""自定义异常体系。"""


class FeishuAgentError(Exception):
    """基础异常。"""


class SignatureInvalidError(FeishuAgentError):
    """飞书事件签名校验失败。"""


class RateLimitExceededError(FeishuAgentError):
    """超过限流阈值。"""


class DuplicateMessageError(FeishuAgentError):
    """webhook 重投，已处理过同一条消息。"""


class LLMCallError(FeishuAgentError):
    """LLM 调用失败。"""


class FeishuAdapterError(FeishuAgentError):
    """飞书适配层错误。"""


class DocWriteError(FeishuAgentError):
    """文档写入失败。"""


class BindDocInvalidError(FeishuAgentError):
    """bind-doc 指令格式或参数无效。"""
```

- [ ] **Step 6: 实现 shared/enums.py**

```python
"""Phase 1 枚举。"""
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCESS = "success"
    SUCCESS_WITH_PARTIAL_FAILURE = "success_with_partial_failure"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocWriteStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    WRITING = "writing"
    SUCCESS = "success"
    FAILED = "failed"
    ORPHANED = "orphaned"
    CANCELLED = "cancelled"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    CLOSED = "closed"


class RiskLevel(str, Enum):
    L0_READ = "L0_read"
    L1_COMPUTE = "L1_compute"
    L2_SIDE_EFFECT = "L2_side_effect"


class ApprovalMode(str, Enum):
    BIND_SCOPE = "bind_scope"
    EXPLICIT_CARD = "explicit_card"
```

- [ ] **Step 7: 实现 shared/schemas.py**

```python
"""Pydantic 数据模型。"""
from typing import Literal

from pydantic import BaseModel, Field


class IncomingMessage(BaseModel):
    """网关归一化后的入站消息。"""
    message_id: str
    chat_id: str
    sender_open_id: str
    text: str
    app_id: str | None = None
    is_bind_doc_cmd: bool = False
    bind_doc_id: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AuditRecord(BaseModel):
    actor_type: Literal["user", "system", "tool"]
    actor_id: str
    action: str
    target_type: str
    target_id: str
    detail: dict = Field(default_factory=dict)
```

- [ ] **Step 8: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_ulid_.py -v`
Expected: 3 passed

- [ ] **Step 9: 提交**

```bash
cd i:\飞书agent
git add shared/ tests/__init__.py tests/unit/__init__.py tests/unit/test_ulid_.py
git commit -m "feat(shared): add ULID, errors, enums, schemas"
```

---## Task 3: PostgreSQL 引擎与表结构

**Files:**
- Create: `persistence/__init__.py`
- Create: `persistence/engine.py`
- Create: `persistence/models.py`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_init.py`
- Create: `alembic.ini`

- [ ] **Step 1: 创建 persistence/__init__.py（空文件）**

- [ ] **Step 2: 实现 persistence/engine.py**

```python
"""SQLAlchemy engine + session factory。"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import load_settings

_settings = load_settings()
_engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


def get_engine():
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务作用域：with 块内任何异常触发 rollback。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 3: 实现 persistence/models.py**

```python
"""SQLAlchemy ORM 模型，对应 PostgreSQL 5 张表。"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_open_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_chat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    bound_doc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bind_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class TaskRow(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage_type: Mapped[str] = mapped_column(String, nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class DocWriteRow(Base):
    __tablename__ = "doc_writes"

    doc_write_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    approval_mode: Mapped[str] = mapped_column(String, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    anchor_block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # app_id:chat_id:message_id
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

- [ ] **Step 4: 实现 migrations/env.py**

```python
"""Alembic 环境配置。"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config.settings import load_settings
from persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = load_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: 实现 migrations/versions/0001_init.py**

```python
"""初始 schema：sessions / tasks / artifacts / doc_writes / audit_logs / idempotency_keys。"""
import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String, primary_key=True),
        sa.Column("owner_open_id", sa.String, nullable=False, index=True),
        sa.Column("source_chat_id", sa.String, nullable=False, index=True),
        sa.Column("bound_doc_id", sa.String, nullable=True),
        sa.Column("bind_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_scope", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, nullable=False, index=True),
        sa.Column("parent_task_id", sa.String, nullable=True),
        sa.Column("message_id", sa.String, nullable=False, index=True),
        sa.Column("intent", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending", index=True),
        sa.Column("plan_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("reply_text", sa.Text, nullable=True),
        sa.Column("error_code", sa.String, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, nullable=False, index=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("storage_type", sa.String, nullable=False),
        sa.Column("storage_ref", sa.Text, nullable=True),
        sa.Column("sha256", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "doc_writes",
        sa.Column("doc_write_id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, nullable=False, index=True),
        sa.Column("doc_id", sa.String, nullable=False),
        sa.Column("requested_by", sa.String, nullable=False),
        sa.Column("approval_mode", sa.String, nullable=False),
        sa.Column("approval_id", sa.String, nullable=True),
        sa.Column("payload_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("anchor_block_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending", index=True),
        sa.Column("fail_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "audit_logs",
        sa.Column("audit_id", sa.String, primary_key=True),
        sa.Column("actor_type", sa.String, nullable=False),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False, index=True),
        sa.Column("target_type", sa.String, nullable=False),
        sa.Column("target_id", sa.String, nullable=False),
        sa.Column("detail_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    for table in ["idempotency_keys", "audit_logs", "doc_writes", "artifacts", "tasks", "sessions"]:
        op.drop_table(table)
```

- [ ] **Step 6: 创建 alembic.ini**

```ini
[alembic]
script_location = migrations
sqlalchemy.url = postgresql://placeholder

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 7: 手工验证 migration**

Run:
```bash
cd i:\飞书agent && alembic upgrade head
```
Expected: 6 tables created

- [ ] **Step 8: 提交**

```bash
cd i:\飞书agent
git add persistence/ migrations/ alembic.ini
git commit -m "feat(persistence): add PostgreSQL schema, ORM models, and initial migration"
```

---

## Task 4: 仓储层（Session / Task / DocWrite / Audit / Idempotency）

**Files:**
- Create: `persistence/repositories/__init__.py`
- Create: `persistence/repositories/session_repo.py`
- Create: `persistence/repositories/task_repo.py`
- Create: `persistence/repositories/doc_write_repo.py`
- Create: `persistence/repositories/audit_repo.py`
- Create: `persistence/repositories/idempotency_repo.py`
- Create: `tests/unit/test_session_repo.py`
- Create: `tests/unit/test_task_repo.py`
- Create: `tests/unit/test_doc_write_repo.py`
- Create: `tests/unit/test_audit_repo.py`

- [ ] **Step 1: 创建 persistence/repositories/__init__.py（空文件）**

- [ ] **Step 2: 写失败测试 — test_session_repo.py（用 SQLite 内存库）**

```python
"""SessionRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.session_repo import SessionRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_upsert_creates_new(session):
    repo = SessionRepo(session)
    row = repo.upsert(
        session_id="s1",
        owner_open_id="ou_x",
        source_chat_id="oc_x",
        bound_doc_id="doc_a",
        bind_expires_at=None,
    )
    assert row.session_id == "s1"
    assert row.bound_doc_id == "doc_a"


def test_upsert_updates_existing(session):
    repo = SessionRepo(session)
    repo.upsert(session_id="s1", owner_open_id="ou_x", source_chat_id="oc_x", bound_doc_id="doc_a", bind_expires_at=None)
    repo.upsert(session_id="s1", owner_open_id="ou_x", source_chat_id="oc_x", bound_doc_id="doc_b", bind_expires_at=None)
    session.commit()
    got = repo.get("s1")
    assert got.bound_doc_id == "doc_b"


def test_get_returns_none_for_unknown(session):
    repo = SessionRepo(session)
    assert repo.get("nonexistent") is None
```

- [ ] **Step 3: 实现 persistence/repositories/session_repo.py**

```python
"""Session 仓储。"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import SessionRow


class SessionRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self,
        session_id: str,
        owner_open_id: str,
        source_chat_id: str,
        bound_doc_id: Optional[str],
        bind_expires_at: Optional[datetime],
    ) -> SessionRow:
        row = self.session.get(SessionRow, session_id)
        if row is None:
            row = SessionRow(
                session_id=session_id,
                owner_open_id=owner_open_id,
                source_chat_id=source_chat_id,
                bound_doc_id=bound_doc_id,
                bind_expires_at=bind_expires_at,
            )
            self.session.add(row)
        else:
            row.bound_doc_id = bound_doc_id
            row.bind_expires_at = bind_expires_at
        self.session.flush()
        return row

    def get(self, session_id: str) -> SessionRow | None:
        return self.session.get(SessionRow, session_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_session_repo.py -v`
Expected: 3 passed

- [ ] **Step 5: 写失败测试 — test_task_repo.py**

```python
"""TaskRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.task_repo import TaskRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_create_and_get_by_message_id(session):
    repo = TaskRepo(session)
    repo.create(
        task_id="t1",
        session_id="s1",
        message_id="om_x",
        intent="general_chat",
    )
    session.commit()
    got = repo.get_by_message_id("om_x")
    assert got is not None
    assert got.task_id == "t1"


def test_update_status_to_success(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x")
    session.commit()
    repo.update_status(task_id="t1", status="success", reply_text="done")
    session.commit()
    got = repo.get("t1")
    assert got.status == "success"
    assert got.reply_text == "done"


def test_update_status_to_failed_with_error(session):
    repo = TaskRepo(session)
    repo.create(task_id="t1", session_id="s1", message_id="om_x")
    session.commit()
    repo.update_status(task_id="t1", status="failed", error_code="LLM_TIMEOUT", error_message="timeout")
    session.commit()
    got = repo.get("t1")
    assert got.status == "failed"
    assert got.error_code == "LLM_TIMEOUT"
```

- [ ] **Step 6: 实现 persistence/repositories/task_repo.py**

```python
"""Task 仓储。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TaskRow


class TaskRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        task_id: str,
        session_id: str,
        message_id: str,
        intent: str | None = None,
        parent_task_id: str | None = None,
        plan_json: dict | None = None,
    ) -> TaskRow:
        row = TaskRow(
            task_id=task_id,
            session_id=session_id,
            message_id=message_id,
            intent=intent,
            parent_task_id=parent_task_id,
            plan_json=plan_json or {},
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, task_id: str) -> TaskRow | None:
        return self.session.get(TaskRow, task_id)

    def get_by_message_id(self, message_id: str) -> TaskRow | None:
        return self.session.query(TaskRow).filter_by(message_id=message_id).first()

    def update_status(
        self,
        task_id: str,
        status: str,
        reply_text: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        row = self.session.get(TaskRow, task_id)
        if row is None:
            return
        row.status = status
        if reply_text is not None:
            row.reply_text = reply_text
        if error_code is not None:
            row.error_code = error_code
        if error_message is not None:
            row.error_message = error_message
        if status in ("success", "success_with_partial_failure", "failed", "cancelled"):
            row.finished_at = datetime.now(timezone.utc)
        self.session.flush()
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_task_repo.py -v`
Expected: 3 passed

- [ ] **Step 8: 写失败测试 — test_doc_write_repo.py**

```python
"""DocWriteRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.doc_write_repo import DocWriteRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_create_pending(session):
    repo = DocWriteRepo(session)
    row = repo.create_pending(
        doc_write_id="dw1",
        task_id="t1",
        doc_id="doc_a",
        requested_by="ou_x",
        approval_mode="bind_scope",
        payload_text="hello",
    )
    assert row.status == "pending"
    assert row.payload_json["text"] == "hello"


def test_transition_pending_to_approved(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.transition(doc_write_id="dw1", to_status="approved")
    session.commit()
    assert repo.get("dw1").status == "approved"


def test_mark_success_records_anchor(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.mark_success(doc_write_id="dw1", anchor_block_id="blk_x")
    session.commit()
    row = repo.get("dw1")
    assert row.status == "success"
    assert row.anchor_block_id == "blk_x"


def test_mark_failed_records_reason(session):
    repo = DocWriteRepo(session)
    repo.create_pending("dw1", "t1", "doc_a", "ou_x", "bind_scope", "hello")
    session.commit()
    repo.mark_failed(doc_write_id="dw1", reason="lark-cli timeout")
    session.commit()
    row = repo.get("dw1")
    assert row.status == "failed"
    assert row.fail_reason == "lark-cli timeout"
```

- [ ] **Step 9: 实现 persistence/repositories/doc_write_repo.py**

```python
"""DocWrite 仓储。"""
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import DocWriteRow


class DocWriteRepo:
    def __init__(self, session: Session):
        self.session = session

    def create_pending(
        self,
        doc_write_id: str,
        task_id: str,
        doc_id: str,
        requested_by: str,
        approval_mode: str,
        payload_text: str,
        approval_id: Optional[str] = None,
    ) -> DocWriteRow:
        row = DocWriteRow(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode=approval_mode,
            approval_id=approval_id,
            payload_json={"text": payload_text},
            status="pending",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, doc_write_id: str) -> DocWriteRow | None:
        return self.session.get(DocWriteRow, doc_write_id)

    def transition(self, doc_write_id: str, to_status: str) -> None:
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = to_status
        self.session.flush()

    def mark_success(self, doc_write_id: str, anchor_block_id: str) -> None:
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = "success"
        row.anchor_block_id = anchor_block_id
        self.session.flush()

    def mark_failed(self, doc_write_id: str, reason: str) -> None:
        row = self.get(doc_write_id)
        if row is None:
            return
        row.status = "failed"
        row.fail_reason = reason
        self.session.flush()
```

- [ ] **Step 10: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_doc_write_repo.py -v`
Expected: 4 passed

- [ ] **Step 11: 写失败测试 — test_audit_repo.py**

```python
"""AuditRepo 测试。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.audit_repo import AuditRepo


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_write_audit_log(session):
    repo = AuditRepo(session)
    repo.write(
        audit_id="a1",
        actor_type="user",
        actor_id="ou_x",
        action="create_task",
        target_type="task",
        target_id="t1",
        detail={"intent": "general_chat"},
    )
    session.commit()
    logs = repo.list_recent(limit=10)
    assert len(logs) == 1
    assert logs[0].audit_id == "a1"


def test_list_recent_orders_desc(session):
    repo = AuditRepo(session)
    for i in range(3):
        repo.write(audit_id=f"a{i}", actor_type="system", actor_id="sys", action="x", target_type="t", target_id=f"id{i}")
    session.commit()
    logs = repo.list_recent(limit=10)
    assert [l.audit_id for l in logs] == ["a2", "a1", "a0"]
```

- [ ] **Step 12: 实现 persistence/repositories/audit_repo.py**

```python
"""Audit 仓储。"""
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import AuditLogRow


class AuditRepo:
    def __init__(self, session: Session):
        self.session = session

    def write(
        self,
        audit_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: Optional[dict] = None,
    ) -> AuditLogRow:
        row = AuditLogRow(
            audit_id=audit_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail_json=detail or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_recent(self, limit: int = 50) -> list[AuditLogRow]:
        return (
            self.session.query(AuditLogRow)
            .order_by(AuditLogRow.created_at.desc())
            .limit(limit)
            .all()
        )
```

- [ ] **Step 13: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_audit_repo.py -v`
Expected: 2 passed

- [ ] **Step 14: 实现 persistence/repositories/idempotency_repo.py**

```python
"""Idempotency 仓储：webhook 去重。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import IdempotencyKeyRow


class IdempotencyRepo:
    def __init__(self, session: Session):
        self.session = session

    def try_reserve(self, key: str, task_id: Optional[str] = None) -> bool:
        """尝试保留幂等键。返回 True 表示首次处理，False 表示重投。"""
        existing = self.session.get(IdempotencyKeyRow, key)
        if existing is not None:
            return False
        row = IdempotencyKeyRow(key=key, task_id=task_id, processed_at=datetime.now(timezone.utc))
        self.session.add(row)
        try:
            self.session.flush()
        except Exception:
            self.session.rollback()
            return False
        return True

    def link_task(self, key: str, task_id: str) -> None:
        row = self.session.get(IdempotencyKeyRow, key)
        if row is not None:
            row.task_id = task_id
            self.session.flush()
```

- [ ] **Step 15: 提交**

```bash
cd i:\飞书agent
git add persistence/repositories/ tests/unit/test_session_repo.py tests/unit/test_task_repo.py tests/unit/test_doc_write_repo.py tests/unit/test_audit_repo.py
git commit -m "feat(persistence): add SessionRepo, TaskRepo, DocWriteRepo, AuditRepo, IdempotencyRepo"
```

---
## Task 5: 飞书 lark-cli 客户端封装 + IM/Doc Adapter

**Files:**
- Create: `feishu_adapter/__init__.py`
- Create: `feishu_adapter/client.py`
- Create: `feishu_adapter/im_adapter.py`
- Create: `feishu_adapter/doc_adapter.py`
- Create: `feishu_adapter/base_projection_adapter.py`
- Create: `tests/unit/test_client.py`
- Create: `tests/unit/test_adapters.py`

- [ ] **Step 1: 创建 feishu_adapter/__init__.py（空文件）**

- [ ] **Step 2: 写失败测试 — test_client.py**

```python
"""lark-cli 调用封装测试。"""
import subprocess
from unittest.mock import patch

import pytest

from feishu_adapter.client import LarkCLI, LarkCLIError


def test_run_success():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"ok": true}', stderr="")
    with patch("subprocess.run", return_value=fake):
        client = LarkCLI()
        assert client.run(["test"]) == {"ok": True}


def test_run_nonzero_raises():
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("subprocess.run", return_value=fake):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI().run(["test"])
        assert "boom" in str(exc.value)


def test_run_timeout_raises():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="lark-cli", timeout=10)):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI(timeout=10).run(["test"])
        assert "timeout" in str(exc.value).lower()


def test_run_non_json_raises():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")
    with patch("subprocess.run", return_value=fake):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI().run(["test"])
        assert "non-json" in str(exc.value).lower()
```

- [ ] **Step 3: 实现 feishu_adapter/client.py**

```python
"""lark-cli subprocess 封装。"""
import json
import shlex
import subprocess
from typing import Any


class LarkCLIError(RuntimeError):
    pass


class LarkCLI:
    def __init__(self, binary: str = "lark-cli", timeout: int = 30):
        self.binary = binary
        self.timeout = timeout

    def run(self, args: list[str]) -> dict[str, Any]:
        cmd = [self.binary, *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise LarkCLIError(f"lark-cli timeout after {self.timeout}s: {shlex.join(cmd)}") from e
        except FileNotFoundError as e:
            raise LarkCLIError(f"lark-cli binary not found: {self.binary}") from e

        if proc.returncode != 0:
            raise LarkCLIError(f"lark-cli exit={proc.returncode} stderr={proc.stderr.strip()}")

        out = proc.stdout.strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise LarkCLIError(f"lark-cli returned non-json output: {out[:200]}") from e
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_client.py -v`
Expected: 4 passed

- [ ] **Step 5: 实现 feishu_adapter/im_adapter.py**

```python
"""IM 消息发送。"""
from feishu_adapter.client import LarkCLI


class IMAdapter:
    def __init__(self, cli: LarkCLI | None = None):
        self.cli = cli or LarkCLI()

    def reply(self, chat_id: str, text: str) -> str:
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", chat_id,
            "--receive-id-type", "chat_id",
            "--msg-type", "text",
            "--content", text,
        ])
        return result.get("message_id", "")

    def send(self, receive_id: str, receive_id_type: str, msg_type: str, content: str) -> str:
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", receive_id,
            "--receive-id-type", receive_id_type,
            "--msg-type", msg_type,
            "--content", content,
        ])
        return result.get("message_id", "")
```

- [ ] **Step 6: 实现 feishu_adapter/doc_adapter.py**

```python
"""Doc 文档操作：Phase 1 仅追加纯文本。"""
from feishu_adapter.client import LarkCLI


class DocAdapter:
    def __init__(self, cli: LarkCLI | None = None):
        self.cli = cli or LarkCLI()

    def get_block_tree(self, doc_id: str) -> list[dict]:
        """读取文档块树，返回有序块列表。"""
        result = self.cli.run(["docx", "block", "list", "--doc-id", doc_id])
        return result.get("blocks", [])

    def append_plain_text(self, doc_id: str, text: str) -> str:
        """在文档末尾追加纯文本块，返回新 block_id。"""
        result = self.cli.run([
            "docx", "block", "create",
            "--doc-id", doc_id,
            "--block-type", "text",
            "--content", text,
        ])
        return result.get("block_id", "")
```

- [ ] **Step 7: 实现 feishu_adapter/base_projection_adapter.py**

```python
"""Base 投影 Adapter：把 PostgreSQL 状态异步写回飞书 Base。

Phase 1：调用失败仅记日志，不影响主事务。
"""
import logging

from feishu_adapter.client import LarkCLI, LarkCLIError

logger = logging.getLogger(__name__)


class BaseProjectionAdapter:
    def __init__(self, app_token: str, cli: LarkCLI | None = None):
        self.app_token = app_token
        self.cli = cli or LarkCLI()

    def project_task(self, task_id: str, status: str, reply_text: str, error_message: str | None) -> bool:
        """投影任务状态到 Base。失败返回 False，由调用方记审计。"""
        try:
            self.cli.run([
                "base", "record", "create",
                "--app-token", self.app_token,
                "--table", "tasks_view",
                "--fields", f"task_id={task_id}",
                "--fields", f"status={status}",
                "--fields", f"reply_text={reply_text[:200] if reply_text else ''}",
                "--fields", f"error_message={error_message or ''}",
            ])
            return True
        except LarkCLIError as e:
            logger.warning("project_task failed task=%s err=%s", task_id, e)
            return False
```

- [ ] **Step 8: 写失败测试 — test_adapters.py**

```python
"""飞书适配层测试（mock lark-cli）。"""
from unittest.mock import patch

import pytest

from feishu_adapter.doc_adapter import DocAdapter
from feishu_adapter.im_adapter import IMAdapter
from feishu_adapter.base_projection_adapter import BaseProjectionAdapter


def test_im_reply_returns_message_id():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"message_id": "om_xxx"}):
        msg_id = IMAdapter().reply(chat_id="oc_x", text="hi")
        assert msg_id == "om_xxx"


def test_doc_append_plain_text_returns_block_id():
    with patch("feishu_adapter.client.LarkCLI.run", return_value={"block_id": "blk_xxx"}):
        block_id = DocAdapter().append_plain_text(doc_id="doc_x", text="hello")
        assert block_id == "blk_xxx"


def test_base_projection_swallows_errors():
    from feishu_adapter.client import LarkCLIError
    with patch("feishu_adapter.client.LarkCLI.run", side_effect=LarkCLIError("boom")):
        result = BaseProjectionAdapter(app_token="bascn_x").project_task(
            task_id="t1", status="success", reply_text="ok", error_message=None
        )
        assert result is False
```

- [ ] **Step 9: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_adapters.py -v`
Expected: 3 passed

- [ ] **Step 10: 提交**

```bash
cd i:\飞书agent
git add feishu_adapter/ tests/unit/test_client.py tests/unit/test_adapters.py
git commit -m "feat(feishu_adapter): add LarkCLI wrapper, IM/Doc/BaseProjection adapters"
```

---

## Task 6: Gateway 层（签名 / 限流 / 幂等 / 归一化）

**Files:**
- Create: `gateway/__init__.py`
- Create: `gateway/signature.py`
- Create: `gateway/rate_limit.py`
- Create: `gateway/idempotency.py`
- Create: `gateway/normalizer.py`
- Create: `tests/unit/test_signature.py`
- Create: `tests/unit/test_rate_limit.py`
- Create: `tests/unit/test_idempotency.py`
- Create: `tests/unit/test_normalizer.py`

- [ ] **Step 1: 创建 gateway/__init__.py（空文件）**

- [ ] **Step 2: 写失败测试 — test_signature.py**

```python
"""飞书事件签名校验测试。"""
import base64
import hashlib
import hmac
import time

import pytest

from gateway.signature import verify_lark_signature
from shared.errors import SignatureInvalidError


SECRET = "test_secret"


def _sign(ts: str, body: str) -> str:
    return base64.b64encode(
        hmac.new(f"{ts}\n{SECRET}\n{body}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()


def test_verify_valid_signature():
    ts = str(int(time.time()))
    body = '{"event":{}}'
    assert verify_lark_signature(timestamp=ts, body=body, signature=_sign(ts, body), secret=SECRET)


def test_verify_expired_timestamp_rejected():
    ts = str(int(time.time()) - 3600)
    body = "{}"
    with pytest.raises(SignatureInvalidError) as exc:
        verify_lark_signature(timestamp=ts, body=body, signature=_sign(ts, body), secret=SECRET, ttl_sec=300)
    assert "expired" in str(exc.value).lower()


def test_verify_wrong_signature_rejected():
    ts = str(int(time.time()))
    with pytest.raises(SignatureInvalidError) as exc:
        verify_lark_signature(timestamp=ts, body="{}", signature="bogus", secret=SECRET)
    assert "mismatch" in str(exc.value).lower()


def test_verify_empty_timestamp_rejected():
    with pytest.raises(SignatureInvalidError):
        verify_lark_signature(timestamp="", body="{}", signature="x", secret=SECRET)
```

- [ ] **Step 3: 实现 gateway/signature.py**

```python
"""飞书事件签名校验。

算法：
  string_to_sign = timestamp + "\\n" + encrypt_key + "\\n" + raw_body
  signature = base64(hmac_sha256(string_to_sign))
"""
import base64
import hashlib
import hmac
import time

from shared.errors import SignatureInvalidError


def verify_lark_signature(timestamp: str, body: str, signature: str, secret: str, ttl_sec: int = 300) -> bool:
    if not timestamp or not signature:
        raise SignatureInvalidError("timestamp or signature empty")
    try:
        ts_int = int(timestamp)
    except ValueError as e:
        raise SignatureInvalidError(f"invalid timestamp: {timestamp}") from e
    if abs(int(time.time()) - ts_int) > ttl_sec:
        raise SignatureInvalidError(f"timestamp expired: ts={ts_int} ttl={ttl_sec}")

    string_to_sign = f"{timestamp}\n{secret}\n{body}".encode()
    expected = base64.b64encode(hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected, signature):
        raise SignatureInvalidError("signature mismatch")
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_signature.py -v`
Expected: 4 passed

- [ ] **Step 5: 写失败测试 — test_rate_limit.py**

```python
"""令牌桶限流测试。"""
import time

import pytest

from gateway.rate_limit import TokenBucket
from shared.errors import RateLimitExceededError


def test_initial_burst_allowed():
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        bucket.acquire(key="app1")


def test_sixth_call_rejected():
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        bucket.acquire(key="app1")
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="app1")


def test_keys_isolated():
    bucket = TokenBucket(capacity=2, refill_per_sec=0.001)
    bucket.acquire(key="a")
    bucket.acquire(key="a")
    bucket.acquire(key="b")
    bucket.acquire(key="b")


def test_refill_after_wait():
    bucket = TokenBucket(capacity=1, refill_per_sec=100.0)
    bucket.acquire(key="a")
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(key="a")
    time.sleep(0.05)
    bucket.acquire(key="a")
```

- [ ] **Step 6: 实现 gateway/rate_limit.py**

```python
"""按 app_id / open_id 的令牌桶限流。Phase 1 进程内实现。"""
import threading
import time

from shared.errors import RateLimitExceededError


class _KeyState:
    __slots__ = ("tokens", "last_refill")

    def __init__(self, capacity: float):
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._buckets: dict[str, _KeyState] = {}
        self._lock = threading.Lock()

    def _refill(self, state: _KeyState) -> None:
        now = time.monotonic()
        elapsed = now - state.last_refill
        if elapsed > 0:
            state.tokens = min(self.capacity, state.tokens + elapsed * self.refill_per_sec)
            state.last_refill = now

    def acquire(self, key: str, cost: float = 1.0) -> None:
        with self._lock:
            state = self._buckets.get(key)
            if state is None:
                state = _KeyState(self.capacity)
                self._buckets[key] = state
            self._refill(state)
            if state.tokens < cost:
                raise RateLimitExceededError(f"rate limit exceeded key={key} tokens={state.tokens:.2f}")
            state.tokens -= cost
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_rate_limit.py -v`
Expected: 4 passed

- [ ] **Step 8: 写失败测试 — test_idempotency.py**

```python
"""Webhook 幂等去重测试。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base
from persistence.repositories.idempotency_repo import IdempotencyRepo


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_first_call_reserved():
    s = _session()
    repo = IdempotencyRepo(s)
    assert repo.try_reserve("app1:oc1:om1") is True
    s.commit()


def test_second_call_rejected():
    s = _session()
    repo = IdempotencyRepo(s)
    repo.try_reserve("app1:oc1:om1")
    s.commit()
    assert repo.try_reserve("app1:oc1:om1") is False


def test_link_task_after_create():
    s = _session()
    repo = IdempotencyRepo(s)
    repo.try_reserve("app1:oc1:om1")
    s.commit()
    repo.link_task("app1:oc1:om1", "t_xxx")
    s.commit()
    # 同一 key 仍应被拒
    assert repo.try_reserve("app1:oc1:om1") is False
```

- [ ] **Step 9: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_idempotency.py -v`
Expected: 3 passed

- [ ] **Step 10: 实现 gateway/idempotency.py**

```python
"""网关层幂等键封装：包装 IdempotencyRepo，提供 build_key 工具。"""
from persistence.repositories.idempotency_repo import IdempotencyRepo


def build_idempotency_key(app_id: str, chat_id: str, message_id: str) -> str:
    return f"{app_id}:{chat_id}:{message_id}"


def is_duplicate(repo: IdempotencyRepo, key: str) -> bool:
    """返回 True 表示已处理过（应直接返回 200，不进入业务）。"""
    return repo.try_reserve(key) is False
```

- [ ] **Step 11: 写失败测试 — test_normalizer.py**

```python
"""Webhook payload → IncomingMessage 归一化测试。"""
import pytest

from gateway.normalizer import normalize_im_event, NormalizeError, parse_bind_doc_cmd


def test_normalize_minimal_text_message():
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": {
                "chat_id": "oc_xxx",
                "message_id": "om_xxx",
                "message_type": "text",
                "content": '{"text": "hello"}',
            },
        },
    }
    msg = normalize_im_event(payload)
    assert msg.message_id == "om_xxx"
    assert msg.text == "hello"
    assert msg.is_bind_doc_cmd is False


def test_parse_bind_doc_cmd_valid():
    text, doc_id = parse_bind_doc_cmd("/bind-doc doccnABC123")
    assert text is None
    assert doc_id == "doccnABC123"


def test_parse_bind_doc_cmd_invalid_returns_none():
    text, doc_id = parse_bind_doc_cmd("hello world")
    assert text is None
    assert doc_id is None


def test_normalize_bind_doc_command_sets_flags():
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": {
                "chat_id": "oc_xxx",
                "message_id": "om_xxx",
                "message_type": "text",
                "content": '{"text": "/bind-doc doccnABC123"}',
            },
        },
    }
    msg = normalize_im_event(payload)
    assert msg.is_bind_doc_cmd is True
    assert msg.bind_doc_id == "doccnABC123"


def test_normalize_skips_non_text():
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_xxx"}},
            "message": {"chat_id": "oc", "message_id": "om", "message_type": "image", "content": "{}"},
        },
    }
    with pytest.raises(NormalizeError) as exc:
        normalize_im_event(payload)
    assert "non-text" in str(exc.value).lower()
```

- [ ] **Step 12: 实现 gateway/normalizer.py**

```python
"""Webhook payload → IncomingMessage 归一化。"""
import json
import re

from shared.errors import FeishuAgentError
from shared.schemas import IncomingMessage


class NormalizeError(FeishuAgentError):
    pass


_BIND_DOC_RE = re.compile(r"^/bind-doc\s+(\S+)\s*$", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+\s*")


def parse_bind_doc_cmd(text: str) -> tuple[str | None, str | None]:
    """识别 /bind-doc <doc_id> 指令。

    返回 (None, doc_id) 表示是 bind-doc 指令；
    返回 (None, None) 表示不是。
    """
    m = _BIND_DOC_RE.match(text.strip())
    if m:
        return None, m.group(1)
    return None, None


def normalize_im_event(payload: dict) -> IncomingMessage:
    try:
        event = payload["event"]
        message = event["message"]
        sender_open_id = event["sender"]["sender_id"]["open_id"]
        chat_id = message["chat_id"]
        message_id = message["message_id"]
        msg_type = message["message_type"]
    except (KeyError, TypeError) as e:
        raise NormalizeError(f"missing required field: {e}") from e

    if msg_type != "text":
        raise NormalizeError(f"skip non-text message_type={msg_type}")

    try:
        text = json.loads(message["content"])["text"]
    except (json.JSONDecodeError, KeyError) as e:
        raise NormalizeError(f"invalid text content: {e}") from e

    if message.get("mentions"):
        text = _MENTION_RE.sub("", text).strip()

    _, doc_id = parse_bind_doc_cmd(text)
    is_bind_cmd = doc_id is not None

    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        text=text,
        is_bind_doc_cmd=is_bind_cmd,
        bind_doc_id=doc_id,
    )
```

- [ ] **Step 13: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_normalizer.py -v`
Expected: 5 passed

- [ ] **Step 14: 提交**

```bash
cd i:\飞书agent
git add gateway/ tests/unit/test_signature.py tests/unit/test_rate_limit.py tests/unit/test_idempotency.py tests/unit/test_normalizer.py
git commit -m "feat(gateway): add signature, rate limit, idempotency, normalizer"
```

---
## Task 7: LLM Router（OpenAI 兼容 + fallback）

**Files:**
- Create: `orchestrator/__init__.py`
- Create: `orchestrator/llm_router.py`
- Create: `tests/unit/test_llm_router.py`

- [ ] **Step 1: 创建 orchestrator/__init__.py（空文件）**

- [ ] **Step 2: 写失败测试 — test_llm_router.py**

```python
"""LLM Router 测试：主成功 / fallback / 全失败。"""
import httpx
import pytest

from orchestrator.llm_router import LLMRouter
from shared.errors import LLMCallError
from shared.schemas import ChatMessage


PRIMARY_RESP = {"choices": [{"message": {"role": "assistant", "content": "primary ok"}}]}
FALLBACK_RESP = {"choices": [{"message": {"role": "assistant", "content": "fallback ok"}}]}


def test_primary_success(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(200, json=PRIMARY_RESP))
    router = LLMRouter(
        primary={"base_url": "http://primary", "api_key": "k1", "model": "m1", "timeout_sec": 5},
        fallback={"base_url": "http://fallback", "api_key": "k2", "model": "m2", "timeout_sec": 5},
    )
    assert router.chat([ChatMessage(role="user", content="hi")]) == "primary ok"


def test_fallback_after_primary_500(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(500, json={"err": "boom"}))
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(200, json=FALLBACK_RESP))
    router = LLMRouter(
        primary={"base_url": "http://primary", "api_key": "k1", "model": "m1", "timeout_sec": 5},
        fallback={"base_url": "http://fallback", "api_key": "k2", "model": "m2", "timeout_sec": 5},
        max_retries=1,
    )
    assert router.chat([ChatMessage(role="user", content="hi")]) == "fallback ok"


def test_both_fail_raises_llm_call_error(respx_mock):
    respx_mock.post("http://primary/chat/completions").mock(return_value=httpx.Response(500, json={"err": "boom"}))
    respx_mock.post("http://fallback/chat/completions").mock(return_value=httpx.Response(502, json={"err": "down"}))
    router = LLMRouter(
        primary={"base_url": "http://primary", "api_key": "k1", "model": "m1", "timeout_sec": 5},
        fallback={"base_url": "http://fallback", "api_key": "k2", "model": "m2", "timeout_sec": 5},
        max_retries=0,
    )
    with pytest.raises(LLMCallError) as exc:
        router.chat([ChatMessage(role="user", content="hi")])
    assert "fallback failed" in str(exc.value).lower()
```

- [ ] **Step 3: 实现 orchestrator/llm_router.py**

```python
"""LLM Router：主备 OpenAI 兼容 API + 自动 fallback。"""
from dataclasses import dataclass
from typing import Iterable

import httpx

from shared.errors import LLMCallError
from shared.schemas import ChatMessage


@dataclass
class _Provider:
    base_url: str
    api_key: str
    model: str
    timeout_sec: int


class LLMRouter:
    def __init__(self, primary: dict, fallback: dict, max_retries: int = 1):
        self.primary = _Provider(**primary)
        self.fallback = _Provider(**fallback)
        self.max_retries = max_retries

    def _call_once(self, provider: _Provider, messages: list[dict]) -> dict:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
        payload = {"model": provider.model, "messages": messages}
        with httpx.Client(timeout=provider.timeout_sec) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def chat(self, messages: Iterable[ChatMessage]) -> str:
        msgs = [{"role": m.role, "content": m.content} for m in messages]
        last_err: Exception | None = None

        for _ in range(self.max_retries + 1):
            try:
                data = self._call_once(self.primary, msgs)
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError) as e:
                last_err = e

        try:
            data = self._call_once(self.fallback, msgs)
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMCallError(f"primary failed ({last_err!r}), fallback failed ({e!r})") from e
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_llm_router.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd i:\飞书agent
git add orchestrator/__init__.py orchestrator/llm_router.py tests/unit/test_llm_router.py
git commit -m "feat(orchestrator): add LLM router with primary/fallback"
```

---

## Task 8: Session / BindDoc / Task / DocWrite 服务层

**Files:**
- Create: `orchestrator/session_service.py`
- Create: `orchestrator/bind_doc_service.py`
- Create: `orchestrator/task_service.py`
- Create: `orchestrator/doc_write_service.py`
- Create: `tests/unit/test_bind_doc_service.py`
- Create: `tests/unit/test_doc_write_service.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: 创建 tests/integration/__init__.py（空文件）**

- [ ] **Step 2: 实现 orchestrator/session_service.py**

```python
"""Session 服务层：创建/查询会话，绑定/解绑文档。"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.ulid_ import new_ulid
from persistence.repositories.session_repo import SessionRepo


class SessionService:
    def __init__(self, repo: SessionRepo):
        self.repo = repo

    def get_or_create(self, owner_open_id: str, source_chat_id: str) -> str:
        """Phase 1 简化：每个 (open_id, chat_id) 复用最新 session，没有就新建。"""
        sid = new_ulid()
        self.repo.upsert(
            session_id=sid,
            owner_open_id=owner_open_id,
            source_chat_id=source_chat_id,
            bound_doc_id=None,
            bind_expires_at=None,
        )
        return sid

    def bind_doc(self, session_id: str, doc_id: str, ttl_sec: int) -> datetime:
        """绑定文档到当前会话，返回过期时间。"""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        self.repo.upsert(
            session_id=session_id,
            owner_open_id="",  # 保留已有 owner
            source_chat_id="",
            bound_doc_id=doc_id,
            bind_expires_at=expires_at,
        )
        return expires_at

    def is_bind_valid(self, session_id: str, doc_id: str) -> bool:
        """检查会话是否对该 doc_id 有有效绑定。"""
        row = self.repo.get(session_id)
        if row is None or row.bound_doc_id != doc_id or row.bind_expires_at is None:
            return False
        return row.bind_expires_at > datetime.now(timezone.utc)

    def bound_doc_id(self, session_id: str) -> Optional[str]:
        """返回当前有效绑定的 doc_id；过期返回 None。"""
        row = self.repo.get(session_id)
        if row is None or row.bound_doc_id is None or row.bind_expires_at is None:
            return None
        if row.bind_expires_at <= datetime.now(timezone.utc):
            return None
        return row.bound_doc_id
```

- [ ] **Step 3: 写失败测试 — test_bind_doc_service.py**

```python
"""bind-doc 服务测试。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orchestrator.bind_doc_service import BindDocService
from shared.errors import BindDocInvalidError


def test_bind_doc_writes_to_session():
    session_svc = MagicMock()
    audit_repo = MagicMock()
    service = BindDocService(session_service=session_svc, audit_repo=audit_repo, ttl_sec=1800)

    expires_at = service.bind(session_id="s1", owner_open_id="ou_x", doc_id="doc_abc")

    session_svc.bind_doc.assert_called_once_with("s1", "doc_abc", 1800)
    audit_repo.write.assert_called_once()
    assert isinstance(expires_at, datetime)
    assert expires_at > datetime.now(timezone.utc)


def test_bind_doc_rejects_empty_doc_id():
    service = BindDocService(session_service=MagicMock(), audit_repo=MagicMock(), ttl_sec=1800)
    from pytest import raises
    with raises(BindDocInvalidError):
        service.bind(session_id="s1", owner_open_id="ou_x", doc_id="")


def test_bind_doc_rejects_invalid_format():
    service = BindDocService(session_service=MagicMock(), audit_repo=MagicMock(), ttl_sec=1800)
    from pytest import raises
    with raises(BindDocInvalidError):
        service.bind(session_id="s1", owner_open_id="ou_x", doc_id="invalid id with spaces")
```

- [ ] **Step 4: 实现 orchestrator/bind_doc_service.py**

```python
"""/bind-doc 服务层。"""
import re
from datetime import datetime

from persistence.repositories.audit_repo import AuditRepo
from shared.errors import BindDocInvalidError
from shared.ulid_ import new_ulid
from orchestrator.session_service import SessionService


_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class BindDocService:
    def __init__(self, session_service: SessionService, audit_repo: AuditRepo, ttl_sec: int):
        self.session_service = session_service
        self.audit_repo = audit_repo
        self.ttl_sec = ttl_sec

    def bind(self, session_id: str, owner_open_id: str, doc_id: str) -> datetime:
        if not doc_id or not _DOC_ID_RE.match(doc_id):
            raise BindDocInvalidError(f"invalid doc_id: {doc_id!r}")
        expires_at = self.session_service.bind_doc(session_id=session_id, doc_id=doc_id, ttl_sec=self.ttl_sec)
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="user",
            actor_id=owner_open_id,
            action="bind_doc",
            target_type="session",
            target_id=session_id,
            detail={"doc_id": doc_id, "expires_at": expires_at.isoformat()},
        )
        return expires_at
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_bind_doc_service.py -v`
Expected: 3 passed

- [ ] **Step 6: 实现 orchestrator/task_service.py**

```python
"""Task 服务层：创建任务、更新状态、写审计。"""
from typing import Optional

from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.task_repo import TaskRepo
from shared.enums import TaskStatus
from shared.ulid_ import new_ulid


class TaskService:
    def __init__(self, task_repo: TaskRepo, audit_repo: AuditRepo):
        self.task_repo = task_repo
        self.audit_repo = audit_repo

    def create(
        self,
        session_id: str,
        message_id: str,
        intent: Optional[str] = None,
        plan_json: Optional[dict] = None,
    ) -> str:
        task_id = new_ulid()
        self.task_repo.create(
            task_id=task_id,
            session_id=session_id,
            message_id=message_id,
            intent=intent,
            plan_json=plan_json,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="create_task",
            target_type="task",
            target_id=task_id,
            detail={"session_id": session_id, "intent": intent},
        )
        return task_id

    def mark_success(self, task_id: str, reply_text: str) -> None:
        self.task_repo.update_status(task_id=task_id, status=TaskStatus.SUCCESS.value, reply_text=reply_text)
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="complete_task",
            target_type="task",
            target_id=task_id,
            detail={"reply_len": len(reply_text)},
        )

    def mark_partial_failure(self, task_id: str, reply_text: str, warning: str) -> None:
        self.task_repo.update_status(
            task_id=task_id,
            status=TaskStatus.SUCCESS_WITH_PARTIAL_FAILURE.value,
            reply_text=reply_text,
            error_code="DOC_WRITE_FAILED",
            error_message=warning,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="complete_task_with_warning",
            target_type="task",
            target_id=task_id,
            detail={"warning": warning},
        )

    def mark_failed(self, task_id: str, error_code: str, error_message: str) -> None:
        self.task_repo.update_status(
            task_id=task_id, status=TaskStatus.FAILED.value, error_code=error_code, error_message=error_message
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="system",
            actor_id="orchestrator",
            action="fail_task",
            target_type="task",
            target_id=task_id,
            detail={"error_code": error_code, "error_message": error_message},
        )
```

- [ ] **Step 7: 写失败测试 — test_doc_write_service.py**

```python
"""DocWriteService 测试：仅在 bind-doc 窗口内允许写。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from orchestrator.doc_write_service import DocWriteService
from shared.errors import DocWriteError


def test_write_to_bound_doc_success():
    session_repo = MagicMock()
    session_repo.get.return_value = MagicMock(
        bound_doc_id="doc_a",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    doc_repo = MagicMock()
    doc_adapter = MagicMock()
    doc_adapter.append_plain_text.return_value = "blk_x"

    svc = DocWriteService(session_repo=session_repo, doc_repo=doc_repo, doc_adapter=doc_adapter)
    result = svc.write_plain_text(
        session_id="s1", task_id="t1", requested_by="ou_x", text="hello"
    )

    assert result["doc_id"] == "doc_a"
    assert result["anchor_block_id"] == "blk_x"
    assert result["status"] == "success"
    doc_repo.create_pending.assert_called_once()
    doc_repo.mark_success.assert_called_once()


def test_write_without_bind_raises():
    session_repo = MagicMock()
    session_repo.get.return_value = MagicMock(bound_doc_id=None, bind_expires_at=None)
    svc = DocWriteService(session_repo=session_repo, doc_repo=MagicMock(), doc_adapter=MagicMock())
    with pytest.raises(DocWriteError) as exc:
        svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="hi")
    assert "no valid bind" in str(exc.value).lower()


def test_write_with_expired_bind_raises():
    session_repo = MagicMock()
    session_repo.get.return_value = MagicMock(
        bound_doc_id="doc_a",
        bind_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    svc = DocWriteService(session_repo=session_repo, doc_repo=MagicMock(), doc_adapter=MagicMock())
    with pytest.raises(DocWriteError):
        svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="hi")
```

- [ ] **Step 8: 实现 orchestrator/doc_write_service.py**

```python
"""文档写入服务层。"""
import logging

from feishu_adapter.doc_adapter import DocAdapter
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from shared.enums import DocWriteStatus
from shared.errors import DocWriteError
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)


class DocWriteService:
    def __init__(self, session_repo: SessionRepo, doc_repo: DocWriteRepo, doc_adapter: DocAdapter):
        self.session_repo = session_repo
        self.doc_repo = doc_repo
        self.doc_adapter = doc_adapter

    def write_plain_text(self, session_id: str, task_id: str, requested_by: str, text: str) -> dict:
        """在 bind-doc 授权窗口内追加纯文本。"""
        session_row = self.session_repo.get(session_id)
        if session_row is None or session_row.bound_doc_id is None:
            raise DocWriteError("no valid bind-doc on this session")

        from datetime import datetime, timezone
        if session_row.bind_expires_at is None or session_row.bind_expires_at <= datetime.now(timezone.utc):
            raise DocWriteError("bind-doc expired, please /bind-doc again")

        doc_id = session_row.bound_doc_id
        doc_write_id = new_ulid()

        # 1. pending
        self.doc_repo.create_pending(
            doc_write_id=doc_write_id,
            task_id=task_id,
            doc_id=doc_id,
            requested_by=requested_by,
            approval_mode="bind_scope",
            payload_text=text,
        )

        # 2. approved
        self.doc_repo.transition(doc_write_id, DocWriteStatus.APPROVED.value)

        # 3. writing
        self.doc_repo.transition(doc_write_id, DocWriteStatus.WRITING.value)

        # 4. 真正写入
        try:
            anchor = self.doc_adapter.append_plain_text(doc_id=doc_id, text=text)
        except Exception as e:
            self.doc_repo.mark_failed(doc_write_id, reason=f"{type(e).__name__}: {e}")
            raise DocWriteError(f"doc write failed: {e}") from e

        # 5. success
        self.doc_repo.mark_success(doc_write_id, anchor_block_id=anchor)

        return {
            "doc_write_id": doc_write_id,
            "doc_id": doc_id,
            "anchor_block_id": anchor,
            "status": "success",
        }
```

- [ ] **Step 9: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/unit/test_doc_write_service.py -v`
Expected: 3 passed

- [ ] **Step 10: 提交**

```bash
cd i:\飞书agent
git add orchestrator/ tests/unit/test_bind_doc_service.py tests/unit/test_doc_write_service.py tests/integration/__init__.py
git commit -m "feat(orchestrator): add Session, BindDoc, Task, DocWrite services"
```

---
## Task 9: Orchestrator 主流程（process() 端到端）

**Files:**
- Create: `orchestrator/app.py`
- Create: `tests/integration/test_message_flow.py`

- [ ] **Step 1: 写失败测试 — test_message_flow.py**

```python
"""Orchestrator.process() 端到端测试（mock LLM + mock lark-cli + 内存 DB）。"""
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.app import Orchestrator
from persistence.models import Base
from shared.schemas import IncomingMessage


def _make_orchestrator():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    from persistence.repositories.session_repo import SessionRepo
    from persistence.repositories.task_repo import TaskRepo
    from persistence.repositories.doc_write_repo import DocWriteRepo
    from persistence.repositories.audit_repo import AuditRepo

    session_repo = SessionRepo(s)
    task_repo = TaskRepo(s)
    doc_repo = DocWriteRepo(s)
    audit_repo = AuditRepo(s)

    from orchestrator.session_service import SessionService
    from orchestrator.task_service import TaskService
    from orchestrator.bind_doc_service import BindDocService
    from orchestrator.doc_write_service import DocWriteService

    session_svc = SessionService(session_repo)
    task_svc = TaskService(task_repo, audit_repo)
    bind_svc = BindDocService(session_svc, audit_repo, ttl_sec=1800)
    doc_adapter = MagicMock()
    doc_adapter.append_plain_text.return_value = "blk_x"
    doc_write_svc = DocWriteService(session_repo, doc_repo, doc_adapter)

    llm_router = MagicMock()
    llm_router.chat.return_value = "你好，我是 AI 助手。"

    im_adapter = MagicMock()
    im_adapter.reply.return_value = "om_reply"

    return (
        Orchestrator(
            llm_router=llm_router,
            session_service=session_svc,
            task_service=task_svc,
            bind_doc_service=bind_svc,
            doc_write_service=doc_write_svc,
            im_adapter=im_adapter,
        ),
        s,
        im_adapter,
    )


def test_process_message_no_bind_only_replies_im():
    orch, s, im = _make_orchestrator()
    incoming = IncomingMessage(
        message_id="om_1", chat_id="oc_1", sender_open_id="ou_1", text="hello"
    )
    result = orch.process(incoming)
    s.commit()
    assert result["status"] == "success"
    assert result["doc_written"] is False
    im.reply.assert_called_once()
    assert "AI 助手" in im.reply.call_args.args[1]


def test_process_bind_doc_command_only_binds_no_llm():
    orch, s, im = _make_orchestrator()
    incoming = IncomingMessage(
        message_id="om_2", chat_id="oc_1", sender_open_id="ou_1",
        text="/bind-doc doccnABC123", is_bind_doc_cmd=True, bind_doc_id="doccnABC123",
    )
    result = orch.process(incoming)
    s.commit()
    assert result["status"] == "bind_doc_success"
    assert result["bound_doc_id"] == "doccnABC123"
    assert "AI" not in im.reply.call_args.args[1]


def test_process_message_with_active_bind_writes_doc():
    orch, s, im = _make_orchestrator()
    # 先 bind
    bind_incoming = IncomingMessage(
        message_id="om_b", chat_id="oc_1", sender_open_id="ou_1",
        text="/bind-doc doccnABC123", is_bind_doc_cmd=True, bind_doc_id="doccnABC123",
    )
    orch.process(bind_incoming)
    s.commit()

    # 再发普通消息
    msg_incoming = IncomingMessage(
        message_id="om_m", chat_id="oc_1", sender_open_id="ou_1", text="hi"
    )
    result = orch.process(msg_incoming)
    s.commit()
    assert result["status"] == "success"
    assert result["doc_written"] is True
    assert result["doc_id"] == "doccnABC123"
```

- [ ] **Step 2: 实现 orchestrator/app.py**

```python
"""Orchestrator 主流程：串联 Session / BindDoc / Task / LLM / IM / Doc-Write。"""
from typing import Optional

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.llm_router import LLMRouter
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from shared.errors import DocWriteError, FeishuAgentError, LLMCallError
from shared.schemas import IncomingMessage, ChatMessage
from shared.ulid_ import new_ulid


SYSTEM_PROMPT = "你是飞书科研助手。请用简洁中文回答，不超过 200 字。"


class Orchestrator:
    def __init__(
        self,
        llm_router: LLMRouter,
        session_service: SessionService,
        task_service: TaskService,
        bind_doc_service: BindDocService,
        doc_write_service: DocWriteService,
        im_adapter: IMAdapter,
    ):
        self.llm = llm_router
        self.session_service = session_service
        self.task_service = task_service
        self.bind_doc_service = bind_doc_service
        self.doc_write_service = doc_write_service
        self.im = im_adapter

    def process(self, incoming: IncomingMessage) -> dict:
        """主入口。返回结果摘要 dict。"""
        # 1. /bind-doc 命令直接走绑定分支
        if incoming.is_bind_doc_cmd and incoming.bind_doc_id:
            return self._handle_bind(incoming)

        # 2. 创建 session + task
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="general_chat",
        )

        # 3. LLM 生成回复
        try:
            reply_text = self.llm.chat([
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=incoming.text),
            ])
        except LLMCallError as e:
            self.task_service.mark_failed(task_id=task_id, error_code="LLM_FAILED", error_message=str(e))
            self.im.reply(incoming.chat_id, f"[错误] LLM 调用失败：{e}")
            return {"status": "failed", "task_id": task_id, "error": str(e)}

        self.im.reply(incoming.chat_id, reply_text)

        # 4. 决定是否写文档
        bound_doc = self.session_service.bound_doc_id(session_id)
        doc_written = False
        doc_id = None
        warning: Optional[str] = None
        if bound_doc:
            try:
                result = self.doc_write_service.write_plain_text(
                    session_id=session_id,
                    task_id=task_id,
                    requested_by=incoming.sender_open_id,
                    text=reply_text,
                )
                doc_written = True
                doc_id = result["doc_id"]
            except DocWriteError as e:
                warning = str(e)

        # 5. 收尾 task 状态
        if warning:
            self.task_service.mark_partial_failure(task_id=task_id, reply_text=reply_text, warning=warning)
        else:
            self.task_service.mark_success(task_id=task_id, reply_text=reply_text)

        return {
            "status": "success" if not warning else "success_with_partial_failure",
            "task_id": task_id,
            "session_id": session_id,
            "reply_text": reply_text,
            "doc_written": doc_written,
            "doc_id": doc_id,
            "warning": warning,
        }

    def _handle_bind(self, incoming: IncomingMessage) -> dict:
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        try:
            expires_at = self.bind_doc_service.bind(
                session_id=session_id,
                owner_open_id=incoming.sender_open_id,
                doc_id=incoming.bind_doc_id,
            )
        except FeishuAgentError as e:
            self.im.reply(incoming.chat_id, f"[错误] bind-doc 失败：{e}")
            return {"status": "bind_doc_failed", "error": str(e)}

        self.im.reply(
            incoming.chat_id,
            f"[成功] 已绑定文档 {incoming.bind_doc_id}，授权有效期至 {expires_at.isoformat()}。"
        )
        return {
            "status": "bind_doc_success",
            "session_id": session_id,
            "bound_doc_id": incoming.bind_doc_id,
            "expires_at": expires_at.isoformat(),
        }
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/integration/test_message_flow.py -v`
Expected: 3 passed

- [ ] **Step 4: 提交**

```bash
cd i:\飞书agent
git add orchestrator/app.py tests/integration/test_message_flow.py
git commit -m "feat(orchestrator): wire process() end-to-end with bind-doc and doc-write"
```

---

## Task 10: FastAPI 网关入口与集成测试

**Files:**
- Create: `gateway/app.py`
- Create: `tests/integration/test_webhook_app.py`
- Create: `测试总结+2026-08-08T20-00-00.md`

- [ ] **Step 1: 写失败测试 — test_webhook_app.py**

```python
"""FastAPI webhook 端到端测试。"""
import base64
import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from gateway.app import create_app


SECRET = "test_secret"


def _sign(ts: str, body: str) -> str:
    return base64.b64encode(
        hmac.new(f"{ts}\n{SECRET}\n{body}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()


def test_health():
    app = create_app(secret=SECRET, orchestrator=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_webhook_invalid_signature_rejected():
    app = create_app(secret=SECRET, orchestrator=MagicMock())
    body = '{"event":{"sender":{"sender_id":{"open_id":"ou_x"}},"message":{"chat_id":"oc_x","message_id":"om_x","message_type":"text","content":"{\\"text\\":\\"hi\\"}"}}}'
    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={"X-Lark-Request-Timestamp": str(int(time.time())), "X-Lark-Signature": "bogus"},
        )
        assert resp.status_code == 401


def test_webhook_valid_signature_dispatches():
    orch = MagicMock()
    orch.process.return_value = {"status": "success", "task_id": "t1", "reply_text": "ok"}
    app = create_app(secret=SECRET, orchestrator=orch)
    body = '{"event":{"sender":{"sender_id":{"open_id":"ou_x"}},"message":{"chat_id":"oc_x","message_id":"om_x","message_type":"text","content":"{\\"text\\":\\"hi\\"}"}}}'
    ts = str(int(time.time()))
    sig = _sign(ts, body)

    with TestClient(app) as client:
        resp = client.post(
            "/webhook/lark",
            content=body,
            headers={
                "X-Lark-Request-Timestamp": ts,
                "X-Lark-Signature": sig,
                "X-Lark-App-Id": "app_test",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        orch.process.assert_called_once()


def test_webhook_duplicate_message_returns_200_no_reprocess():
    """幂等：同一条 webhook 重投应直接返回 200，不再调用 orchestrator.process。"""
    orch = MagicMock()
    orch.process.return_value = {"status": "success"}
    app = create_app(secret=SECRET, orchestrator=orch)
    body = '{"event":{"sender":{"sender_id":{"open_id":"ou_x"}},"message":{"chat_id":"oc_x","message_id":"om_dup","message_type":"text","content":"{\\"text\\":\\"hi\\"}"}}}'
    ts = str(int(time.time()))
    sig = _sign(ts, body)
    headers = {
        "X-Lark-Request-Timestamp": ts,
        "X-Lark-Signature": sig,
        "X-Lark-App-Id": "app_test",
    }

    with TestClient(app) as client:
        r1 = client.post("/webhook/lark", content=body, headers=headers)
        r2 = client.post("/webhook/lark", content=body, headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"
        assert orch.process.call_count == 1
```

- [ ] **Step 2: 实现 gateway/app.py**

```python
"""FastAPI 网关入口。"""
import logging
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request

from gateway.idempotency import build_idempotency_key
from gateway.normalizer import normalize_im_event, NormalizeError
from gateway.rate_limit import TokenBucket
from gateway.signature import verify_lark_signature
from persistence.engine import SessionLocal
from persistence.repositories.idempotency_repo import IdempotencyRepo
from shared.errors import (
    DuplicateMessageError,
    FeishuAgentError,
    RateLimitExceededError,
    SignatureInvalidError,
)

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    secret: str
    rate: TokenBucket
    rate_per_min: int


def create_app(secret: str, orchestrator, rate_per_min: int = 60) -> FastAPI:
    app = FastAPI(title="Feishu Research Agent — Phase 1")
    app.state.ctx = AppContext(
        secret=secret,
        rate=TokenBucket(capacity=rate_per_min, refill_per_sec=rate_per_min / 60.0),
        rate_per_min=rate_per_min,
    )
    app.state.orchestrator = orchestrator

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/webhook/lark")
    async def lark_webhook(request: Request):
        ctx: AppContext = app.state.ctx
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")

        # 1. 签名校验
        ts = request.headers.get("X-Lark-Request-Timestamp", "")
        sig = request.headers.get("X-Lark-Signature", "")
        try:
            verify_lark_signature(timestamp=ts, body=body_str, signature=sig, secret=ctx.secret)
        except SignatureInvalidError as e:
            raise HTTPException(status_code=401, detail=str(e))

        # 2. 限流
        app_id = request.headers.get("X-Lark-App-Id", "default")
        try:
            ctx.rate.acquire(key=app_id)
        except RateLimitExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))

        # 3. 归一化
        try:
            payload = await request.json()
            incoming = normalize_im_event(payload)
        except NormalizeError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 4. 幂等去重
        idem_key = build_idempotency_key(app_id, incoming.chat_id, incoming.message_id)
        with SessionLocal() as s:
            idem_repo = IdempotencyRepo(s)
            if not idem_repo.try_reserve(idem_key):
                # 已处理过
                return {"status": "duplicate", "idempotency_key": idem_key}

        # 5. 业务处理
        try:
            result = app.state.orchestrator.process(incoming)
        except FeishuAgentError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # 6. 关联 task_id 到幂等键（便于追溯）
        task_id = result.get("task_id") if isinstance(result, dict) else None
        if task_id:
            with SessionLocal() as s:
                idem_repo = IdempotencyRepo(s)
                idem_repo.link_task(idem_key, task_id)
                s.commit()

        return result

    return app
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd i:\飞书agent && pytest tests/integration/test_webhook_app.py -v`
Expected: 4 passed

- [ ] **Step 4: 跑全部测试**

Run:
```bash
cd i:\飞书agent && pytest tests/ -v --tb=short
```
Expected: 全部通过；用例数 33+（unit）+ 7+（integration）

- [ ] **Step 5: 计算覆盖率**

Run:
```bash
cd i:\飞书agent && pip install pytest-cov
cd i:\飞书agent && pytest tests/ --cov=gateway --cov=orchestrator --cov=feishu_adapter --cov=persistence --cov=shared --cov-report=term-missing
```
Expected: 总覆盖率 ≥60%

- [ ] **Step 6: 创建测试总结文件**

创建 `测试总结+2026-08-08T20-00-00.md`（项目根目录）：

````markdown
# 测试总结 — 2026-08-08 20:00

## Phase 1 首测结果

### 测试用例数
- tests/unit/: 30 个
- tests/integration/: 7 个
- 合计: 37 个，全部通过

### 覆盖率
- gateway: XX%
- orchestrator: XX%
- feishu_adapter: XX%
- persistence: XX%
- shared: XX%
- 总计: XX%

### 测试场景覆盖
- [x] 签名校验（合法 / 过期 / 错签 / 空）
- [x] 限流（突发 / 隔离 key / 续桶）
- [x] Webhook 幂等（首次保留 / 重投拒绝 / link_task）
- [x] Webhook 归一化（普通 / bind-doc / 非文本 / 缺字段）
- [x] LLM Router（主成功 / fallback / 全部失败）
- [x] PostgreSQL 仓储（Session / Task / DocWrite / Audit / Idempotency）
- [x] BindDoc 服务（合法 / 空 doc_id / 格式错）
- [x] DocWrite 服务（窗口内成功 / 无绑定拒绝 / 过期拒绝）
- [x] Orchestrator 端到端（普通消息 / bind-doc / 有绑定写文档）
- [x] FastAPI webhook（含签名 / 限流 / 幂等 / dispatch / 重投）

### 未覆盖场景（已知）
- 真实飞书 webhook 投递（需测试租户）
- 真实 LLM API 调用（未跑通）
- 长任务 / 大文件上传（Phase 2+ 引入沙箱后覆盖）
- 并发压力（Phase 5 引入 Redis 后覆盖）
- PostgreSQL 迁移在 CI 上的运行（需 docker）

### 后续建议
1. 接入真实飞书测试租户跑端到端
2. 接入真实 LLM API（DeepSeek/Qwen）验证 fallback
3. 给 Orchestrator 加 token 计数，避免超出 200k 限制
4. 引入 OpenTelemetry trace，方便调试
5. 增加 pytest-xdist 并行跑测试
6. PostgreSQL 迁移进 GitHub Actions
````

- [ ] **Step 7: 提交**

```bash
cd i:\飞书agent
git add gateway/app.py tests/integration/test_webhook_app.py "测试总结+2026-08-08T20-00-00.md"
git commit -m "feat(gateway): add FastAPI webhook with signature, rate limit, idempotency"
```

---

## 验收标准（Definition of Done）

- [ ] 所有单元与集成测试通过（37+ 用例）
- [ ] 覆盖率 ≥60%（仅 Phase 1 代码）
- [ ] 手动跑通：飞书私聊发消息 → 5 秒内 IM 回复
- [ ] 手动验证：`/bind-doc <doc_id>` 后 30 分钟内普通消息回复会追加到该文档
- [ ] 手动验证：LLM 主模型宕机时自动切到备用模型
- [ ] 手动验证：签名错误返回 401
- [ ] 手动验证：同一条消息重投不重复写文档
- [ ] PostgreSQL `sessions / tasks / doc_writes / audit_logs / idempotency_keys` 正确写入
- [ ] 测试总结文件已建立

## 不做的事（明确边界）

- 不接 Docker 沙箱（Phase 2）
- 不做 DAG 条件分支（Phase 3）
- 不做工具注册中心 / 插件机制（Phase 2）
- 不接 Vault（Phase 5）
- 不做 Drive Adapter（Phase 2）
- 不做图片 / 表格富文本写入（Phase 2+）
- 不做群聊多人协同写同一文档
- 不做完整的 IM 卡片审批流（Phase 1 用 bind-doc 前置授权代替）

## Phase 2 入口

Phase 1 稳定后启动：
1. Executor + Docker 沙箱
2. Drive Adapter（大文件回传）
3. 卡片审批流（替代 bind-doc 前置授权）
4. 结构化产物（表格 / 图片 / 代码块）
5. 多步 DAG + 工具插件
6. Base 看板增强

参考 [`../specs/2026-08-08-feishu-research-agent-phase2-5-archive.md`](../specs/2026-08-08-feishu-research-agent-phase2-5-archive.md)。
