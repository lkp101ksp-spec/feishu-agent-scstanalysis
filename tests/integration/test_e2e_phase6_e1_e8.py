"""E1-E8: Phase 6 端到端场景。"""
import json as _json
import subprocess as _subprocess
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gateway.app import create_app
from orchestrator.blocks.schemas import (CalloutBlock, DividerBlock,
                                          EmbedBlock, EquationBlock,
                                          HeadingBlock, MathBlock,
                                          MermaidBlock, TextBlock,
                                          VideoBlock)
from orchestrator.templates.share_service import ShareService
from orchestrator.templates.template_service import TemplateService
from orchestrator.templates.version_service import VersionService
from persistence.models import Base
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
    ts = TemplateService(repo=t_repo)
    vs = VersionService(version_repo=v_repo, template_repo=t_repo)
    ss = ShareService(template_repo=t_repo)
    return ts, vs, ss, t_repo, v_repo


# E1: 富文本 14 类 → 飞书 doc
@respx.mock
def test_e1_phase6_blocks_rendered():
    respx.post("https://example.feishu.cn/docx/v1/blocks").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    from feishu_adapter.doc_adapter import DocAdapter

    class NoWaitLimiter:
        def wait(self):
            pass

    adapter = DocAdapter(base_url="https://example.feishu.cn", api_token="t",
                          rate_limiter=NoWaitLimiter())
    blocks = [
        HeadingBlock(level=2, text="Report"),
        DividerBlock(),
        CalloutBlock(emoji="📌", text="Note"),
        EmbedBlock(url="https://example.com", title="Ref"),
        EquationBlock(latex="E = mc^2"),
        MathBlock(latex="\\sum x", display_mode=True),
        MermaidBlock(code="graph TD; A-->B"),
        VideoBlock(url="https://v.mp4"),
        TextBlock(text="end"),
    ]
    adapter.render_blocks("doc_1", blocks)
    assert respx.calls.call_count == 9


# E2: 模板版本：3 次更新 → 列表 → 回滚
def test_e2_template_versioning_and_rollback():
    ts, vs, _, t_repo, _ = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="std",
                          blocks=[TextBlock(text="v1")], description="")
    # ts.create_block 不自动写版本（Phase 6 简化）；手动触发
    vs.on_template_upsert(template_id=tid, name="std", description="",
                          blocks_json="[]", steps_json=None,
                          created_by="ou_1")
    tpl = t_repo.get(tid)
    tpl.blocks_json = '[{"type": "text", "text": "v2"}]'
    vs.on_template_upsert(template_id=tid, name="std", description="",
                          blocks_json=tpl.blocks_json, steps_json=None,
                          created_by="ou_1")
    versions = vs.list_versions(tid)
    assert len(versions) == 2
    vs.rollback(template_id=tid, version_number=1, caller_open_id="ou_1")
    versions_after = vs.list_versions(tid)
    assert len(versions_after) == 3  # rollback 写新版本


# E3: 版本硬上限 10
def test_e3_version_hard_limit_10():
    ts, vs, _, t_repo, _ = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="t",
                          blocks=[TextBlock(text="v0")], description="")
    for i in range(12):
        vs.on_template_upsert(template_id=tid, name="t", description="",
                              blocks_json=None, steps_json=None,
                              created_by="ou_1")
    assert len(vs.list_versions(tid)) == 10


# E4: 群聊共享
def test_e4_chat_share_visibility():
    ts, _, ss, _, _ = _make_full()
    tid = ts.create_block(owner_open_id="ou_1", name="x",
                          blocks=[TextBlock(text="x")], description="")
    ss.share_to_chat(template_id=tid, chat_id="chat_1",
                     caller_open_id="ou_1")
    assert ss.can_access(template_id=tid, caller_open_id="ou_2",
                          chat_id="chat_1") is True
    assert ss.can_access(template_id=tid, caller_open_id="ou_2",
                          chat_id="chat_2") is False


# E5: 本地 BLAST：解析 JSON 输出
def test_e5_local_blast_parse():
    from orchestrator.tools.bio.blast_local import BlastLocalTool
    fake_out = _json.dumps({
        "BlastOutput2": [{
            "report": {"results": {"search": {"hits": [
                {"num": 1, "description": [{"title": "BRCA1"}],
                 "hsps": [{"hsp_score": 100}]},
        ]}}}
        }]
    })
    tmpdir = tempfile.mkdtemp()
    fake_binary = tmpdir + "/blastp.bat"
    with open(fake_binary, "w") as f:
        f.write("@echo off\n")
    for ext in (".phr", ".psq"):
        with open(tmpdir + "/nr" + ext, "w") as f:
            f.write("")
    tool = BlastLocalTool(binary_path=fake_binary, db_path=tmpdir)
    with patch.object(_subprocess, "run",
                       return_value=_subprocess.CompletedProcess(
                           args=[], returncode=0, stdout=fake_out, stderr="")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["records"][0]["title"] == "BRCA1"


# E6: 模式自动切换
def test_e6_blast_mode_auto():
    from orchestrator.tools.bio.blast_local import BlastLocalTool
    tmpdir = tempfile.mkdtemp()
    tool = BlastLocalTool(binary_path="/nonexistent/blastp",
                          db_path=tmpdir)
    assert tool._resolve_mode(database="nr") == "web"


# E7: 热加载：管理员上传 → AST P0 拦截 → 注册
def test_e7_hot_loader_safe_tool():
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.hot_loader import HotLoader
    from orchestrator.tools.tool_registry import ToolRegistry
    reg = ToolRegistry()
    loader = HotLoader(
        tool_registry=reg, audit_repo=MagicMock(),
        ast_guard=ASTGuard(),
        temp_dir=tempfile.mkdtemp(),
    )
    code = "def handle(seq): return {'rc': seq[::-1]}"
    name = loader.upload(
        name="revcomp", code=code,
        parameters={"type": "object"},
        risk_level="L0_read", actor_open_id="admin_1",
    )
    assert name == "revcomp"
    assert reg.get("revcomp") is not None


# E8: 热加载：含 eval → 拦截 + audit
def test_e8_hot_loader_blocks_eval():
    from orchestrator.tools.ast_guard import ASTGuard
    from orchestrator.tools.hot_loader import HotLoader
    from orchestrator.tools.tool_registry import ToolRegistry
    from shared.errors import ToolBlockedError
    reg = ToolRegistry()
    audit = MagicMock()
    loader = HotLoader(
        tool_registry=reg, audit_repo=audit,
        ast_guard=ASTGuard(),
        temp_dir=tempfile.mkdtemp(),
    )
    code = "def handle(): return eval('1+1')"
    with pytest.raises(ToolBlockedError):
        loader.upload(
            name="bad", code=code,
            parameters={"type": "object"},
            risk_level="L0_read", actor_open_id="admin_1",
        )
    audit.write.assert_called()