"""doc_writes 增加 anchor_text 列：记录每次写入生效的锚点文字。

联调新增（#写到 消息级锚点）：同锚点的多次写入按上次写入块顺序续排，
不同锚点互不跟随，靠本列区分。
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_doc_writes_anchor_text"
down_revision = "0003_sessions_bind_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("doc_writes", sa.Column("anchor_text", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("doc_writes", "anchor_text")
