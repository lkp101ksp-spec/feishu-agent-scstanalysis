"""长会话记忆：messages 表（2026-09-08 spec）。"""
import sqlalchemy as sa
from alembic import op

revision = "0006_messages"
down_revision = "0005_llm_active"
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
