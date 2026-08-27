"""SQLAlchemy ORM 模型，对应 PostgreSQL 6 张表。

注意：JSON 字段在生产 PostgreSQL 应改为 JSONB 以获得索引能力；
Phase 1 用 JSON 保持 SQLite/PostgreSQL 双兼容。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    """UTC 当前时间，用于默认时间戳。"""
    return datetime.now(timezone.utc)


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_open_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_chat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    bound_doc_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bind_anchor: Mapped[str | None] = mapped_column(String, nullable=True)
    bind_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # === Phase 3 ===
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    origin_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    token_count: Mapped[int] = mapped_column(default=0, nullable=False)


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # === Phase 2 ===
    file_token: Mapped[str | None] = mapped_column(String, nullable=True)
    drive_url: Mapped[str | None] = mapped_column(String, nullable=True)
    mime: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(default=None, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # 本次写入生效的锚点文字（会话级或消息级）：同锚点续写跟随定位用
    anchor_text: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


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


# === Phase 2 ===

class ExecutionRow(Base):
    """节点级执行记录：每个 DAG 节点执行一次一行。

    状态机（与 shared.executor_types.ExecutionState 一致）：
    pending / running / success / failed / skipped / cancelled / denied
    """

    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    inputs_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    outputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # === Phase 3 ===
    loop_id: Mapped[str | None] = mapped_column(String, nullable=True)
    loop_iteration: Mapped[int | None] = mapped_column(default=None, nullable=True)
    dynamic_parent_id: Mapped[str | None] = mapped_column(String, nullable=True)


class ApprovalRow(Base):
    """卡片审批记录（含 nonce 一次性约束）。"""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    args_preview: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor_open_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    nonce: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# === Phase 3 ===

class PlanRuntimeStateRow(Base):
    """PlanRuntime 持久化状态（loop counter / dynamic nodes / iteration vars）。"""
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
    """session 冻结记录（新旧 session 关联）。"""
    __tablename__ = "session_freezes"

    freeze_id: Mapped[str] = mapped_column(String, primary_key=True)
    origin_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    new_session_id: Mapped[str] = mapped_column(String, nullable=False)
    summary_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger_ratio: Mapped[float] = mapped_column(default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


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
    # === Phase 6 ===
    scope: Mapped[str] = mapped_column(String, default="user", nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # === Phase 7 ===
    lineage_template_id: Mapped[str | None] = mapped_column(String, nullable=True)


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


# === Phase 7 ===

class TemplateAuditRow(Base):
    """Phase 7: 公共模板审核日志。"""
    __tablename__ = "template_audit"

    audit_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    actor_open_id: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# === Phase 8 ===

class CommentRow(Base):
    """Phase 8: 评论本地快照（飞书 doc comment 按需同步，幂等 upsert）。

    comment_id 为飞书侧生成（reply 用 reply_id），天然唯一作幂等键；
    processed_at 不被 sync 覆盖（防动作重放，ADR-0019/0023）。
    """
    __tablename__ = "comments"

    comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(String, default="", nullable=False)
    user_name: Mapped[str] = mapped_column(String, default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_reply: Mapped[bool] = mapped_column(default=False, nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TemplateTagRow(Base):
    """Phase 8: 模板标签（多对多，unique(template_id, tag)，ADR-0022）。"""
    __tablename__ = "template_tags"
    __table_args__ = (
        UniqueConstraint("template_id", "tag", name="uq_template_tag"),
    )

    tag_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class TemplateFavoriteRow(Base):
    """Phase 8: 模板收藏（user↔template，unique(template_id, user_open_id)）。"""
    __tablename__ = "template_favorites"
    __table_args__ = (
        UniqueConstraint("template_id", "user_open_id", name="uq_template_favorite"),
    )

    favorite_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_open_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# === Phase 9 ===

class CommentNotifyRow(Base):
    """Phase 9: 评论动作推送日志（同一 comment 只通知一次，ADR-0025）。"""
    __tablename__ = "comment_notify_log"

    comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
