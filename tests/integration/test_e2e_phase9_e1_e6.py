"""Phase 9 T9: E2E E1-E6（真 SQLite + 真 service + stub client + mock IM）。

E1 自动闭环（sync + 推送）/ E2 推送去重 / E3 推送后 apply /
E4 融合排序 / E5 标签组合 / E6 diff moved
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.auto_sync_worker import CommentAutoSyncWorker
from orchestrator.templates.comment_action_service import CommentActionService
from orchestrator.templates.comment_sync_service import CommentSyncService
from orchestrator.templates.diff_service import VersionDiffService
from orchestrator.templates.notify_service import CommentNotifyService
from orchestrator.templates.unified_search_service import UnifiedSearchService
from orchestrator.templates.version_service import VersionService
from persistence.models import Base
from persistence.repositories.comment_notify_repo import CommentNotifyRepo
from persistence.repositories.comment_repo import CommentRepo
from persistence.repositories.session_repo import SessionRepo
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo


class _Ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    class StubClient:
        def __init__(self):
            self.items = []

        def list_comments(self, *, doc_id):
            return self.items

    client = StubClient()
    im = MagicMock()
    comment_repo = CommentRepo(session)
    template_repo = TemplateRepo(session)
    version_repo = TemplateVersionRepo(session)
    version_service = VersionService(version_repo, template_repo)

    sync = CommentSyncService(client, comment_repo)
    notify = CommentNotifyService(comment_repo, CommentNotifyRepo(session), im)
    worker = CommentAutoSyncWorker(
        session_repo=SessionRepo(session),
        sync_service=sync, notify_service=notify, interval_sec=60,
    )
    search = UnifiedSearchService(
        template_repo,
        tag_repo=TemplateTagRepo(session),
        favorite_repo=TemplateFavoriteRepo(session),
    )
    action = CommentActionService(comment_repo, template_repo, version_service)
    diff = VersionDiffService(version_repo, template_repo)

    # 活跃绑定 session（doc_1 ↔ oc_1/ou_owner）
    SessionRepo(session).upsert(
        session_id="s1", owner_open_id="ou_owner", source_chat_id="oc_1",
        bound_doc_id="doc_1",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    return _Ns(
        session=session, client=client, im=im,
        sync=sync, notify=notify, worker=worker, search=search,
        action=action, diff=diff,
        comment_repo=comment_repo, template_repo=template_repo,
        version_repo=version_repo, version_service=version_service,
    )


# E1: 自动闭环：stub 评论 → tick → sync 落库 + IM 推送 owner
def test_e1_auto_loop_sync_and_notify(env):
    env.client.items.append({
        "comment_id": "c1", "user_id": "ou_mentor", "user_name": "导师",
        "text": "/replan t1 增加对照组",
    })
    out = env.worker.tick()
    assert out == {"synced": 1, "notified_total": 1}
    assert len(env.sync.list_stored(doc_id="doc_1")) == 1
    env.im.reply.assert_called_once()
    assert "/comment-apply doc_1" in env.im.reply.call_args.args[1]


# E2: 推送去重：同评论再 tick 不重发
def test_e2_notify_dedup_on_next_tick(env):
    env.client.items.append({
        "comment_id": "c1", "user_name": "导师", "text": "/replan t1 x",
    })
    env.worker.tick()
    second = env.worker.tick()
    assert second["notified_total"] == 0
    assert env.im.reply.call_count == 1


# E3: 推送后 apply 闭环 → 后续 tick 无新 pending
def test_e3_apply_after_notify(env):
    env.client.items.append({
        "comment_id": "c1", "user_name": "导师", "text": "/replan t1 调整样本量",
    })
    env.worker.tick()
    # owner 依提示应用
    TemplateRepo(env.session).upsert(
        template_id="t1", owner_open_id="ou_owner", name="std",
        type_="subplan", steps_json="[]", description="原始",
    )
    out = env.action.apply(doc_id="doc_1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    # 再 tick：评论已 processed，不进 pending
    third = env.worker.tick()
    assert third["notified_total"] == 0
    assert env.im.reply.call_count == 1  # 仍只有第一次推送


# E4: 融合排序：弱匹配 + 高收藏 排在 强匹配 + 零收藏 前
def test_e4_fusion_ranking(env):
    env.template_repo.upsert(
        template_id="t_strong", owner_open_id="ou_1", name="blast 流程",
        type_="block", description="x",
    )
    env.template_repo.upsert(
        template_id="t_hot", owner_open_id="ou_1", name="某流程",
        type_="block", description="含 blast",
    )
    fav = TemplateFavoriteRepo(env.session)
    for i in range(3):
        fav.add(favorite_id=f"f{i}", template_id="t_hot",
                user_open_id=f"ou_{i}")
    out = env.search.search(query="blast")
    assert out[0]["template_id"] == "t_hot"
    assert out[0]["score"] == 2.5  # 1.0 + 3*0.5
    assert out[0]["favorite_count"] == 3


# E5: 标签 + 全文组合过滤
def test_e5_tag_and_text_combined(env):
    env.template_repo.upsert(
        template_id="t_hit", owner_open_id="ou_1", name="blast 流程",
        type_="block", description="",
    )
    env.template_repo.upsert(
        template_id="t_no_tag", owner_open_id="ou_1", name="blast 其他",
        type_="block", description="",
    )
    TemplateTagRepo(env.session).add(
        tag_id="g1", template_id="t_hit", tag="bio", created_by="ou_1")
    out = env.search.search(query="blast", tag="bio")
    assert [r["template_id"] for r in out] == ["t_hit"]
    assert out[0]["tags"] == ["bio"]


# E6: diff moved：块换位不误报 changed
def test_e6_diff_moved(env):
    env.template_repo.upsert(
        template_id="t1", owner_open_id="ou_1", name="std",
        type_="block",
        blocks_json=json.dumps([
            {"type": "heading", "text": "A"},
            {"type": "code", "language": "py"},
        ]),
    )
    env.version_service.on_template_upsert(
        template_id="t1", name="std", description="",
        blocks_json=json.dumps([
            {"type": "heading", "text": "A"},
            {"type": "code", "language": "py"},
        ]),
        created_by="ou_1",
    )
    # v2：两块换位
    env.template_repo.upsert(
        template_id="t1", owner_open_id="ou_1", name="std",
        type_="block",
        blocks_json=json.dumps([
            {"type": "code", "language": "py"},
            {"type": "heading", "text": "A"},
        ]),
    )
    env.version_service.on_template_upsert(
        template_id="t1", name="std", description="",
        blocks_json=json.dumps([
            {"type": "code", "language": "py"},
            {"type": "heading", "text": "A"},
        ]),
        created_by="ou_1",
    )
    diff = env.diff.diff(template_id="t1", v_a=1, v_b=2)
    assert len(diff["blocks"]["moved"]) == 2
    assert diff["blocks"]["changed"] == []
    assert "↔" in env.diff.render(diff)
