"""初始 schema：sessions / tasks / artifacts / doc_writes / audit_logs / idempotency_keys。"""
import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    """反向迁移：按依赖反序删除。"""
    for table in ["idempotency_keys", "audit_logs", "doc_writes", "artifacts", "tasks", "sessions"]:
        op.drop_table(table)
