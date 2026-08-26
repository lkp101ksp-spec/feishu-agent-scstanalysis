"""Phase 8 T10: E2E E1-E8（真 SQLite + 真 repo/service + client stub）。

E1 replan 闭环 / E2 add-step / E3 越权 / E4 幂等 /
E5 revise + diff / E6 标签检索 / E7 收藏 / E8 展平 + block 锚定
"""
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.comment_action_service import CommentActionService
from orchestrator.templates.comment_sync_service import CommentSyncService
from orchestrator.templates.diff_service import VersionDiffService
from orchestrator.templates.favorite_service import FavoriteService
from orchestrator.templates.tag_service import TagService
from orchestrator.templates.version_service import VersionService
from persistence.models import Base
from persistence.repositories.comment_repo import CommentRepo
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo


@pytest.fixture
def env():
    """真库 + 真服务 + stub 飞书 client。"""
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
    comment_repo = CommentRepo(session)
    template_repo = TemplateRepo(session)
    version_repo = TemplateVersionRepo(session)
    version_service = VersionService(version_repo, template_repo)

    # 建模板 t1（subplan，owner=ou_owner）并写初始版本 1
    template_repo.upsert(
        template_id="t1", owner_open_id="ou_owner", name="std",
        type_="subplan", steps_json="[]", description="初始描述",
    )
    version_service.on_template_upsert(
        template_id="t1", name="std", description="初始描述",
        blocks_json=None, steps_json="[]", created_by="ou_owner",
    )

    return SimpleNamespace_(
        session=session, client=client,
        sync=CommentSyncService(client, comment_repo),
        action=CommentActionService(comment_repo, template_repo, version_service),
        diff=VersionDiffService(version_repo, template_repo),
        tag=TagService(TemplateTagRepo(session), template_repo),
        fav=FavoriteService(TemplateFavoriteRepo(session), template_repo),
        comment_repo=comment_repo, template_repo=template_repo,
        version_repo=version_repo,
    )


class SimpleNamespace_:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _post(env, cid, text, user="导师", block_id=None, replies=None):
    env.client.items.append({
        "comment_id": cid, "block_id": block_id,
        "user_id": "ou_mentor", "user_name": user, "text": text,
        "replies": replies or [],
    })


# E1: 导师评论 /replan → sync → owner apply → 版本 +1 + 打标
def test_e1_replan_full_loop(env):
    _post(env, "c1", "/replan t1 增加对照组")
    env.sync.sync(doc_id="d1")
    out = env.action.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    tpl = env.template_repo.get("t1")
    assert "[replan by ou_owner] 增加对照组" in tpl.description
    assert env.version_repo.count("t1") == 2  # 版本 bump
    assert env.comment_repo.list_by_doc("d1")[0].processed_at is not None


# E2: /add-step → steps +1 + 版本 +1
def test_e2_add_step(env):
    _post(env, "c1", "/add-step t1 blast_local")
    env.sync.sync(doc_id="d1")
    out = env.action.apply(doc_id="d1", caller_open_id="ou_owner")
    assert out["applied"] == 1
    steps = json.loads(env.template_repo.get("t1").steps_json)
    assert steps == [{"tool": "blast_local", "args": {}}]
    assert env.version_repo.count("t1") == 2


# E3: 非 owner apply → failed，模板未变
def test_e3_non_owner_rejected(env):
    _post(env, "c1", "/replan t1 越权内容")
    env.sync.sync(doc_id="d1")
    out = env.action.apply(doc_id="d1", caller_open_id="ou_attacker")
    assert out["failed"] == 1
    assert out["details"][0]["reason"] == "not_owner"
    assert env.template_repo.get("t1").description == "初始描述"
    assert env.version_repo.count("t1") == 1


# E4: 重复 apply → applied=0（幂等）
def test_e4_apply_idempotent(env):
    _post(env, "c1", "/replan t1 第一次")
    env.sync.sync(doc_id="d1")
    first = env.action.apply(doc_id="d1", caller_open_id="ou_owner")
    second = env.action.apply(doc_id="d1", caller_open_id="ou_owner")
    assert first["applied"] == 1
    assert second["applied"] == 0
    assert env.version_repo.count("t1") == 2  # 只 bump 一次


# E5: /revise 产生版本 2，diff(v1, v2) 显示 description 变更
def test_e5_revise_then_diff(env):
    _post(env, "c1", "/revise t1 修订后的研究计划描述")
    env.sync.sync(doc_id="d1")
    env.action.apply(doc_id="d1", caller_open_id="ou_owner")
    diff = env.diff.diff(template_id="t1", v_a=1, v_b=2)
    assert diff["meta"]["description_changed"] is True
    rendered = env.diff.render(diff)
    assert "~ description 已变更" in rendered


# E6: tag → find_by_tag 命中
def test_e6_tag_search_loop(env):
    env.tag.attach(template_id="t1", tag="BLAST", caller_open_id="ou_owner")
    hits = env.tag.find_by_tag("blast")  # 查询侧也归一化
    assert [t.template_id for t in hits] == ["t1"]
    assert env.tag.list_tags("t1") == ["blast"]


# E7: favorite → list_favorites 命中 + 幂等
def test_e7_favorite_loop(env):
    env.fav.favorite(template_id="t1", caller_open_id="ou_student")
    env.fav.favorite(template_id="t1", caller_open_id="ou_student")  # 幂等
    hits = env.fav.list_favorites("ou_student")
    assert [t.template_id for t in hits] == ["t1"]


# E8: root+reply 展平落库 + block_id 锚定过滤
def test_e8_flatten_and_block_anchor(env):
    _post(env, "c1", "整体不错", block_id="b1", replies=[
        {"reply_id": "r1", "user_name": "学生", "text": "收到"},
    ])
    _post(env, "c2", "这里要改", block_id="b2")
    out = env.sync.sync(doc_id="d1")
    assert out["fetched"] == 3
    rows_b1 = env.sync.list_stored(doc_id="d1", block_id="b1")
    assert [r.comment_id for r in rows_b1] == ["c1", "r1"]
    reply = [r for r in rows_b1 if r.is_reply][0]
    assert reply.parent_comment_id == "c1"
    rows_b2 = env.sync.list_stored(doc_id="d1", block_id="b2")
    assert [r.comment_id for r in rows_b2] == ["c2"]
