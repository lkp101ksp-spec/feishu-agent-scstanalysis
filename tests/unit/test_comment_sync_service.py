"""Phase 8 T4: CommentSyncService 单元测试（SQLite 真库 + client stub）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.comment_sync_service import CommentSyncService
from persistence.models import Base
from persistence.repositories.comment_repo import CommentRepo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


class _StubClient:
    """飞书 CommentClient stub：可注入 items。"""

    def __init__(self, items):
        self.items = items

    def list_comments(self, *, doc_id):
        return self.items


def _items():
    return [
        {
            "comment_id": "c1",
            "block_id": "b1",
            "user_id": "ou_mentor",
            "user_name": "导师",
            "text": "/replan t1 增加对照组",
            "resolved": False,
            "replies": [
                {
                    "reply_id": "r1",
                    "user_id": "ou_student",
                    "user_name": "学生",
                    "text": "收到",
                },
                {
                    "reply_id": "r2",
                    "user_id": "ou_student",
                    "user_name": "学生",
                    "text": "已改",
                },
            ],
        }
    ]


def test_sync_flattens_root_and_replies(session):
    svc = CommentSyncService(_StubClient(_items()), CommentRepo(session))
    out = svc.sync(doc_id="d1")
    rows = svc.list_stored(doc_id="d1")
    assert len(rows) == 3
    root = [r for r in rows if not r.is_reply]
    replies = [r for r in rows if r.is_reply]
    assert len(root) == 1 and len(replies) == 2
    assert all(r.parent_comment_id == "c1" for r in replies)


def test_sync_idempotent_resync(session):
    svc = CommentSyncService(_StubClient(_items()), CommentRepo(session))
    first = svc.sync(doc_id="d1")
    second = svc.sync(doc_id="d1")
    assert first == {"fetched": 3, "new": 3, "updated": 0, "deleted": 0}
    assert second == {"fetched": 3, "new": 0, "updated": 3, "deleted": 0}
    assert len(svc.list_stored(doc_id="d1")) == 3


def test_sync_empty_comments(session):
    svc = CommentSyncService(_StubClient([]), CommentRepo(session))
    out = svc.sync(doc_id="d1")
    assert out == {"fetched": 0, "new": 0, "updated": 0, "deleted": 0}
    assert svc.list_stored(doc_id="d1") == []


def test_sync_mixed_new_and_updated_counts(session):
    repo = CommentRepo(session)
    svc = CommentSyncService(_StubClient(_items()), repo)
    svc.sync(doc_id="d1")
    # 追加一条新 root 评论再同步
    svc.client.items.append({
        "comment_id": "c2", "user_name": "U", "text": "新评论",
    })
    out = svc.sync(doc_id="d1")
    assert out == {"fetched": 4, "new": 1, "updated": 3, "deleted": 0}


# --- Phase 18：删除对账（远端消失的评论本地清除） ---


def test_sync_deletes_stale_local_rows(session):
    """远端已删评论：sync 对账后本地行被清除，deleted 计数正确。"""
    repo = CommentRepo(session)
    svc = CommentSyncService(_StubClient(_items()), repo)
    svc.sync(doc_id="d1")
    assert repo.get("r2") is not None

    # 远端删除 r2（replies 只剩 r1）后再同步
    svc.client.items[0]["replies"] = svc.client.items[0]["replies"][:1]
    out = svc.sync(doc_id="d1")
    assert out["deleted"] == 1
    assert repo.get("r2") is None
    assert repo.get("c1") is not None and repo.get("r1") is not None


def test_sync_delete_scoped_to_same_doc(session):
    """对账只影响同 doc 的行；其他 doc 的评论不受影响。"""
    repo = CommentRepo(session)
    repo.upsert_one(comment_id="other_doc_c", doc_id="d2", text="别删我")
    svc = CommentSyncService(_StubClient([]), repo)
    out = svc.sync(doc_id="d1")
    assert out["deleted"] == 0
    assert repo.get("other_doc_c") is not None


def test_sync_delete_all_when_remote_empty(session):
    """远端评论清空后本地该 doc 快照全删。"""
    repo = CommentRepo(session)
    svc = CommentSyncService(_StubClient(_items()), repo)
    svc.sync(doc_id="d1")
    svc.client.items = []
    out = svc.sync(doc_id="d1")
    assert out["deleted"] == 3
    assert svc.list_stored(doc_id="d1") == []
