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
