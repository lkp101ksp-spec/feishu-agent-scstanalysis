"""E1-E8: Phase 7 端到端场景。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.comment_service import CommentService
from orchestrator.templates.fork_service import ForkService
from orchestrator.templates.public_service import PublicTemplateService
from orchestrator.templates.search_service import TemplateSearchService
from orchestrator.templates.template_service import TemplateService
from persistence.models import Base
from persistence.repositories.template_audit_repo import TemplateAuditRepo
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo


def _make_full():
    engine = create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    t_repo = TemplateRepo(session)
    v_repo = TemplateVersionRepo(session)
    a_repo = TemplateAuditRepo(session)
    ts = TemplateService(repo=t_repo)
    ss = TemplateSearchService(template_repo=t_repo)
    ps = PublicTemplateService(
        template_repo=t_repo, audit_repo=MagicMock(),
        admin_user_ids={"admin_1"},
    )
    fs = ForkService(template_repo=t_repo)
    return ts, ss, ps, fs, t_repo, a_repo


class _StubCommentClient:
    """E1/E2 桩客户端：返回扁平结构，验证 CommentService 渲染/过滤逻辑。"""

    def __init__(self, comments):
        self._comments = comments

    def list_comments(self, *, doc_id):
        return self._comments

    def list_block_comments(self, *, doc_id, block_id):
        return [c for c in self._comments if c.get("block_id") == block_id]


def test_e1_comments_fetch_thread():
    client = _StubCommentClient([{
        "comment_id": "c1", "user_name": "张三", "text": "Hi",
        "block_id": "b1", "replies": [
            {"user_name": "李四", "text": "回复"},
        ],
    }])
    service = CommentService(client=client)
    text = service.fetch_thread(doc_id="doc_1")
    assert "张三" in text
    assert "李四" in text


def test_e2_comments_filter_by_block():
    client = _StubCommentClient([
        {"comment_id": "c1", "block_id": "b1", "text": "x"},
        {"comment_id": "c2", "block_id": "b2", "text": "y"},
    ])
    service = CommentService(client=client)
    out = service.fetch_thread(doc_id="doc_1", block_id="b1")
    assert "x" in out
    assert "y" not in out


def test_e3_template_search():
    ts, ss, _, _, t_repo, _ = _make_full()
    t_repo.upsert(template_id="t1", owner_open_id="ou_1",
                  name="blast_workflow", type_="subplan",
                  blocks_json=None, steps_json="[]", description="x")
    t_repo.upsert(template_id="t2", owner_open_id="ou_1",
                  name="std_report", type_="block", blocks_json="[]",
                  steps_json=None, description="y")
    session = t_repo.session
    session.commit()
    out = ss.search(query="blast")
    assert len(out) == 1
    assert out[0].name == "blast_workflow"


def test_e4_public_template_approve_flow():
    ts, _, ps, _, t_repo, a_repo = _make_full()
    from orchestrator.blocks.schemas import TextBlock
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                           blocks=[TextBlock(text="hi")], description="x")
    ps.submit_for_review(template_id=tid, actor_open_id="ou_1")
    ps.approve(template_id=tid, actor_open_id="admin_1")
    tpl = t_repo.get(tid)
    assert tpl.scope == "public"


def test_e5_public_template_reject_flow():
    ts, _, ps, _, t_repo, a_repo = _make_full()
    from orchestrator.blocks.schemas import TextBlock
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                           blocks=[TextBlock(text="hi")], description="x")
    ps.submit_for_review(template_id=tid, actor_open_id="ou_1")
    ps.reject(template_id=tid, actor_open_id="admin_1", reason="不完整")
    tpl = t_repo.get(tid)
    assert tpl.scope == "user"


def test_e6_non_admin_cannot_approve():
    ts, _, ps, _, _, _ = _make_full()
    from orchestrator.blocks.schemas import TextBlock
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                           blocks=[TextBlock(text="hi")], description="x")
    ps.submit_for_review(template_id=tid, actor_open_id="ou_1")
    with pytest.raises(PermissionError):
        ps.approve(template_id=tid, actor_open_id="ou_2")


def test_e7_fork_public_to_private():
    ts, _, ps, fs, t_repo, _ = _make_full()
    from orchestrator.blocks.schemas import TextBlock
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                           blocks=[TextBlock(text="hi")], description="x")
    ps.submit_for_review(template_id=tid, actor_open_id="ou_1")
    ps.approve(template_id=tid, actor_open_id="admin_1")
    new_id = fs.fork_from_public(
        source_template_id=tid, actor_open_id="ou_2",
    )
    new_tpl = t_repo.get(new_id)
    assert new_tpl.owner_open_id == "ou_2"
    assert new_tpl.scope == "user"
    assert new_tpl.lineage_template_id == tid


def test_e8_fork_non_public_rejected():
    ts, _, _, fs, _, _ = _make_full()
    from orchestrator.blocks.schemas import TextBlock
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                           blocks=[TextBlock(text="hi")], description="x")
    with pytest.raises(PermissionError):
        fs.fork_from_public(source_template_id=tid,
                             actor_open_id="ou_2")
