"""sessions 增加 bind_anchor 列：/bind-doc <链接> @锚点 的写入位置记忆。

联调新增（锚点定位写入）：绑定文档时可指定章节锚点文字，
后续 doc 写入插到锚点块之后、并按上次写入位置顺序续排。
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_sessions_bind_anchor"
down_revision = "0002_phase2_to_phase9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("bind_anchor", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "bind_anchor")
