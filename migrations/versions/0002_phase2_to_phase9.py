"""Phase 2-9 表补齐：12 张新表 + sessions 扩列 + templates GIN 全文索引。

对齐 persistence/models.py 的 Base.metadata（真源）：
- Phase 2: executions / approvals
- Phase 3: plan_runtime_state / session_freezes / sessions+archived_at/origin_session_id/token_count
- Phase 5-8: templates / template_versions / template_audit / comments
             / template_tags / template_favorites
- Phase 9: comment_notify_log + templates GIN（tsvector，ADR-0029）
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_to_phase9"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def _now() -> sa.func:
    return sa.func.now()


def upgrade() -> None:
    # --- Phase 3: sessions 扩列 ---
    op.add_column("sessions", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("origin_session_id", sa.String, nullable=True))
    op.add_column("sessions", sa.Column("token_count", sa.Integer, nullable=False, server_default="0"))

    # --- Phase 2: executions ---
    op.create_table(
        "executions",
        sa.Column("execution_id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, nullable=False, index=True),
        sa.Column("plan_id", sa.String, nullable=False, index=True),
        sa.Column("node_id", sa.String, nullable=False),
        sa.Column("tool_name", sa.String, nullable=True),
        sa.Column("tool_version", sa.String, nullable=True),
        sa.Column("risk_level", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False, server_default="pending", index=True),
        sa.Column("inputs_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("outputs_json", sa.JSON, nullable=True),
        sa.Column("artifacts_ids", sa.JSON, nullable=True),
        sa.Column("approval_id", sa.String, nullable=True),
        sa.Column("error_code", sa.String, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loop_id", sa.String, nullable=True),
        sa.Column("loop_iteration", sa.Integer, nullable=True),
        sa.Column("dynamic_parent_id", sa.String, nullable=True),
    )

    # --- Phase 2: approvals ---
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, nullable=False, index=True),
        sa.Column("tool_name", sa.String, nullable=False),
        sa.Column("args_preview", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("actor_open_id", sa.String, nullable=False),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("nonce", sa.String, nullable=False, unique=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending", index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("resolved_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Phase 3: plan_runtime_state / session_freezes ---
    op.create_table(
        "plan_runtime_state",
        sa.Column("plan_id", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, nullable=True),
        sa.Column("state_json", sa.JSON, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )
    op.create_table(
        "session_freezes",
        sa.Column("freeze_id", sa.String, primary_key=True),
        sa.Column("origin_session_id", sa.String, nullable=False, index=True),
        sa.Column("new_session_id", sa.String, nullable=False),
        sa.Column("summary_id", sa.String, nullable=True),
        sa.Column("trigger_ratio", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    # --- Phase 5: templates ---
    op.create_table(
        "templates",
        sa.Column("template_id", sa.String, primary_key=True),
        sa.Column("owner_open_id", sa.String, nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("blocks_json", sa.Text, nullable=True),
        sa.Column("steps_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String, nullable=False, server_default="user"),
        sa.Column("chat_id", sa.String, nullable=True),
        sa.Column("lineage_template_id", sa.String, nullable=True),
    )

    # --- Phase 6: template_versions ---
    op.create_table(
        "template_versions",
        sa.Column("version_id", sa.String, primary_key=True),
        sa.Column("template_id", sa.String, nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("blocks_json", sa.Text, nullable=True),
        sa.Column("steps_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("created_by", sa.String, nullable=False),
    )

    # --- Phase 7: template_audit ---
    op.create_table(
        "template_audit",
        sa.Column("audit_id", sa.String, primary_key=True),
        sa.Column("template_id", sa.String, nullable=False, index=True),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("actor_open_id", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    # --- Phase 8: comments / template_tags / template_favorites ---
    op.create_table(
        "comments",
        sa.Column("comment_id", sa.String, primary_key=True),
        sa.Column("doc_id", sa.String, nullable=False, index=True),
        sa.Column("block_id", sa.String, nullable=True),
        sa.Column("user_id", sa.String, nullable=False, server_default=""),
        sa.Column("user_name", sa.String, nullable=False, server_default=""),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("is_reply", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("parent_comment_id", sa.String, nullable=True),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "template_tags",
        sa.Column("tag_id", sa.String, primary_key=True),
        sa.Column("template_id", sa.String, nullable=False, index=True),
        sa.Column("tag", sa.String, nullable=False, index=True),
        sa.Column("created_by", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("template_id", "tag", name="uq_template_tag"),
    )
    op.create_table(
        "template_favorites",
        sa.Column("favorite_id", sa.String, primary_key=True),
        sa.Column("template_id", sa.String, nullable=False, index=True),
        sa.Column("user_open_id", sa.String, nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.UniqueConstraint("template_id", "user_open_id", name="uq_template_favorite"),
    )

    # --- Phase 9: comment_notify_log ---
    op.create_table(
        "comment_notify_log",
        sa.Column("comment_id", sa.String, primary_key=True),
        sa.Column("doc_id", sa.String, nullable=False, index=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )

    # --- Phase 9: templates GIN 全文索引（ADR-0029）---
    op.execute(
        "CREATE INDEX ix_templates_fts ON templates "
        "USING GIN (to_tsvector('simple', "
        "coalesce(name, '') || ' ' || coalesce(description, '')))"
    )


def downgrade() -> None:
    op.drop_index("ix_templates_fts", table_name="templates")
    op.drop_table("comment_notify_log")
    op.drop_table("template_favorites")
    op.drop_table("template_tags")
    op.drop_table("comments")
    op.drop_table("template_audit")
    op.drop_table("template_versions")
    op.drop_table("templates")
    op.drop_table("session_freezes")
    op.drop_table("plan_runtime_state")
    op.drop_table("approvals")
    op.drop_table("executions")
    op.drop_column("sessions", "token_count")
    op.drop_column("sessions", "origin_session_id")
    op.drop_column("sessions", "archived_at")
