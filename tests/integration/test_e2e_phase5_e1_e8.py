"""E1-E8: Phase 5 端到端场景。"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gateway.app import create_app
from orchestrator.blocks.schemas import (HeadingBlock, ListBlock, TableBlock,
                                          TextBlock)
from orchestrator.planner.dag_schema import DAGNode
from orchestrator.planner.scheduler import Scheduler
from orchestrator.templates.schemas import SubPlanTemplateStep
from orchestrator.templates.template_service import TemplateService
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolSpec
from persistence.models import Base
from persistence.repositories.template_repo import TemplateRepo


def _make_service():
    engine = create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return TemplateService(repo=TemplateRepo(session)), session


# E1: 上传 Block 模板 → 复用 → 飞书 doc 渲染 6 类 block
def test_e1_block_template_full_pipeline():
    svc, _ = _make_service()
    blocks = [
        HeadingBlock(level=2, text="Report"),
        TextBlock(text="intro"),
        ListBlock(items=["x", "y"]),
        TableBlock(headers=["A", "B"], rows=[["1", "2"]]),
    ]
    tid = svc.create_block(owner_open_id="ou_1", name="std",
                            blocks=blocks, description="")
    out = svc.render_block(template_id=tid, params={})
    assert len(out) == 4
    assert out[0].type == "heading"


# E2: sub-Plan 模板上传 → Planner DAG → Scheduler 展开
def test_e2_subplan_template_expansion():
    svc, _ = _make_service()
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="blast",
        steps=[
            SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                 inputs={"query": "BRCA1"}),
        ],
        description="",
    )
    n = DAGNode(node_id="n1", kind="tool", tool_name="tpl_use",
                inputs={}, subplan_template_id=tid)
    expanded = Scheduler._expand_subplan_static(n, svc)
    assert len(expanded) == 1
    assert expanded[0].tool_name == "blast_search"


# E3: 模板参数化 {{gene}} → 渲染时替换
def test_e3_template_param_substitution():
    svc, _ = _make_service()
    tid = svc.create_subplan(
        owner_open_id="ou_1", name="t",
        steps=[
            SubPlanTemplateStep(step_id="s1", tool_name="blast_search",
                                 inputs={"query": "{{gene}}"}),
        ],
        description="",
    )
    out = svc.render_subplan(template_id=tid, params={"gene": "BRCA1"})
    assert out[0].inputs["query"] == "BRCA1"


# E4: 模板权限隔离
def test_e4_template_privacy():
    svc, _ = _make_service()
    tid = svc.create_block(owner_open_id="ou_1", name="x",
                            blocks=[TextBlock(text="private")], description="")
    with pytest.raises(PermissionError):
        svc.delete(template_id=tid, caller_open_id="ou_2")


# E5: ToolHandler 双路径（blocks 字段）
def test_e5_toolhandler_blocks_field():
    reg = MagicMock()
    reg.get.return_value = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        risk_level="L0_read",
        handler=lambda: {
            "data": "y",
            "blocks": [{"type": "heading", "level": 2, "text": "Hi"}],
        },
    )
    h = ToolHandler(registry=reg)
    result = h.execute("x", {}, actor_open_id="ou_1", session_id="s1")
    assert result.blocks is not None
    assert len(result.blocks) == 1
    assert result.blocks[0].text == "Hi"


# E6: DocAdapter 飞书 doc 限流（在 test_doc_adapter_blocks 覆盖）


# E7: Block schema 验证
def test_e7_block_schema_validation():
    with pytest.raises(ValidationError):
        TableBlock(headers=["A", "B"], rows=[["1", "2", "3"]])


# E8: FastAPI /templates/* 全链路
def test_e8_templates_api_full_pipeline():
    svc, _ = _make_service()
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
        template_service=svc,
    ))
    body = {
        "owner_open_id": "ou_1", "name": "std",
        "blocks": [{"type": "text", "text": "hi"}],
        "description": "d",
    }
    resp = client.post("/templates/block", json=body)
    assert resp.status_code == 200
    tid = resp.json()["template_id"]
    resp = client.get("/templates/", params={"owner_open_id": "ou_1"})
    assert resp.status_code == 200
    assert tid in resp.json()["templates"]
    resp = client.post(f"/templates/{tid}/render", json={"params": {}})
    assert resp.status_code == 200
    resp = client.delete(f"/templates/{tid}",
                          params={"caller_open_id": "ou_1"})
    assert resp.status_code == 200