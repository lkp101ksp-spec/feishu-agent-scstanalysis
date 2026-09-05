"""Phase 30 模型切换：llm_active 单行表（当前生效主备候选名）。

只存 providers 名字（api key 不落库）；空表 = 未切换过，
回退 config/llm.yaml router 段默认主备。
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_llm_active"
down_revision = "0004_doc_writes_anchor_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_active",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("primary_name", sa.String(), nullable=False),
        sa.Column("fallback_name", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_active")
