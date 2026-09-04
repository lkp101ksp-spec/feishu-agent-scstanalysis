import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app


@pytest.fixture
def client_with_bind_doc_service():
    bind_doc_service = MagicMock()
    from datetime import datetime, timedelta, timezone
    bind_doc_service.renew.return_value = datetime.now(timezone.utc) + timedelta(seconds=1800)
    client = TestClient(create_app(
        secret="phase2-secret",
        orchestrator=object(),
        bind_doc_service=bind_doc_service,
    ))
    return client, bind_doc_service


def test_card_renew_callback_routes_to_bind_doc_service(client_with_bind_doc_service):
    client, bind_doc_service = client_with_bind_doc_service
    body = json.dumps({
        "action": "renew_bind",
        "session_id": "s1",
        "open_id": "ou_1",
    }).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body_data = resp.json()
    assert body_data["ok"] is True
    bind_doc_service.renew.assert_called_once_with(session_id="s1")


def test_card_non_renew_action_does_not_call_renew(client_with_bind_doc_service):
    client, bind_doc_service = client_with_bind_doc_service
    body = json.dumps({"action": "approve", "approval_id": "a1",
                       "open_id": "ou_1"}).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    # 非 renew_bind action 不应触发 renew
    bind_doc_service.renew.assert_not_called()


# === Phase 14：research_writeback 分支 ===

@pytest.fixture
def client_with_broker():
    """内存 SQLite（含 dw1 → requested_by=ou_1）+ 真 ApprovalBroker。

    Phase 15 T1 起回调需查 doc_writes.requested_by 做 operator 校验，
    session_factory 必须注入，避免测试连真实 PG。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from orchestrator.approval_broker import ApprovalBroker
    from persistence.models import Base
    from persistence.repositories.doc_write_repo import DocWriteRepo

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        DocWriteRepo(s).create_pending(
            doc_write_id="dw1", task_id="t1", doc_id="doc1",
            requested_by="ou_1", approval_mode="card_confirm",
            payload_text="preview",
        )
        s.commit()
    finally:
        s.close()

    broker = ApprovalBroker()
    app = create_app(
        secret="phase2-secret",
        orchestrator=object(),
        approval_broker=broker,
    )
    app.state.session_factory = factory
    client = TestClient(app)
    return client, broker


def _post_card(client, payload: dict):
    body = json.dumps(payload).encode()
    secret = "phase2-dev-secret-change-me"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook/lark/card",
        content=body,
        headers={"X-Lark-Signature": sig, "Content-Type": "application/json"},
    )


def test_research_writeback_decide_reaches_broker(client_with_broker):
    """发起者首次点击：决策写入 broker，research 线程 wait 能取到。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_duplicate_click_rejected(client_with_broker):
    """发起者重复点击：幂等拒绝，首个决策不被覆盖。"""
    client, broker = client_with_broker
    _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                        "decision": "approve", "open_id": "ou_1"})
    resp = _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                               "decision": "deny", "open_id": "ou_1"})
    assert resp.json() == {"ok": False, "status": "already_handled",
                           "decision": ""}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_research_writeback_click_after_finish_already_handled(
        client_with_broker):
    """持久化幂等兜底：决策被 wait 消费且行已终态 → already_handled。

    真机 2026-09-01：broker 条目被 wait 取走后二次点击曾再返回 decided。
    gateway 查 doc_writes.status 非 pending 时直接拦截。
    """
    client, broker = client_with_broker
    # 模拟首轮完整流程：点击 → wait 消费 → 行转终态
    _post_card(client, {"action": "research_writeback", "doc_write_id": "dw1",
                        "decision": "approve", "open_id": "ou_1"})
    assert broker.wait("dw1", timeout=0.1) == "approve"
    from persistence.repositories.doc_write_repo import DocWriteRepo
    app = client.app
    s = app.state.session_factory()
    try:
        DocWriteRepo(s).mark_success("dw1", anchor_block_id="blk_1")
        s.commit()
    finally:
        s.close()
    # 二次点击：行状态 success ≠ pending → already_handled（不再进 broker）
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.json() == {"ok": False, "status": "already_handled",
                           "decision": ""}


def test_research_writeback_non_owner_forbidden(client_with_broker):
    """Phase 15 T1：非发起者点击 → forbidden，决策不进 broker。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_other",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    # broker 未收到决策（wait 超时返回 None）
    assert broker.wait("dw1", timeout=0.1) is None


def test_research_writeback_unknown_doc_write_passes(client_with_broker):
    """row 缺失（重启后孤儿/未知 id）：owner 查不到不拦截，走 broker 原逻辑。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw_unknown",
        "decision": "deny", "open_id": "ou_anyone",
    })
    assert resp.json() == {"ok": True, "status": "decided", "decision": "deny"}
    assert broker.wait("dw_unknown", timeout=0.1) == "deny"


def test_research_writeback_without_broker_not_configured():
    client = TestClient(create_app(
        secret="phase2-secret", orchestrator=object(),
    ))
    resp = _post_card(client, {
        "action": "research_writeback", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.json()["ok"] is False
    assert "not configured" in resp.json()["reason"]


# === Phase 17：node_l2_approval 分支（write_doc 节点审批） ===

def test_node_l2_approval_decide_reaches_broker(client_with_broker):
    """发起者点同意：决策写入 broker（同一分支逻辑，action 不同）。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "node_l2_approval", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_1",
    })
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("dw1", timeout=0.1) == "approve"


def test_node_l2_approval_non_owner_forbidden(client_with_broker):
    """非发起者点击 node_l2_approval → forbidden（owner 校验复用）。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "node_l2_approval", "doc_write_id": "dw1",
        "decision": "approve", "open_id": "ou_other",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    assert broker.wait("dw1", timeout=0.1) is None


# === Phase 26：code_approval 分支 ===


def test_code_approval_decide_reaches_broker(client_with_broker):
    """owner 匹配：决策写入 broker，coding 线程 wait 能取到。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca1",
        "decision": "approve", "open_id": "ou_1", "owner": "ou_1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("ca1", timeout=0.1) == "approve"


def test_code_approval_owner_mismatch_forbidden(client_with_broker):
    """非发起者点击：forbidden，不写 broker。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca2",
        "decision": "approve", "open_id": "ou_2", "owner": "ou_1",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    assert broker.wait("ca2", timeout=0.1) is None


def test_code_approval_deny_propagates(client_with_broker):
    """拒绝决策同样可达 coding 线程。"""
    client, broker = client_with_broker
    _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca3",
        "decision": "deny", "open_id": "ou_1", "owner": "ou_1",
    })
    assert broker.wait("ca3", timeout=0.1) == "deny"


def test_code_approval_duplicate_click_idempotent(client_with_broker):
    """重复点击：幂等 already_handled，首决策不被覆盖。"""
    client, broker = client_with_broker
    _post_card(client, {"action": "code_approval", "code_approval_id": "ca4",
                        "decision": "approve", "open_id": "ou_1", "owner": "ou_1"})
    resp = _post_card(client, {"action": "code_approval", "code_approval_id": "ca4",
                               "decision": "deny", "open_id": "ou_1", "owner": "ou_1"})
    assert resp.json()["status"] == "already_handled"
    assert broker.wait("ca4", timeout=0.1) == "approve"


# === Phase 27：skill_improve 分支 ===


@pytest.fixture
def client_with_skill_diagnoser(tmp_path):
    """真 ApprovalBroker + 真 SkillDiagnoser（tmp 假 skill 目录）的卡片环境。"""
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from orchestrator.approval_broker import ApprovalBroker
    from orchestrator.coding.skill_diagnoser import SkillDiagnoser
    from persistence.models import Base

    skill_dir = tmp_path / "skills" / "reportgen"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reportgen\ndescription: 生成报表\n---\n正文。\n",
        encoding="utf-8")
    (skill_dir / "tools.yaml").write_text(
        "tools:\n  - name: run_report\n    timeout_sec: 60\n", encoding="utf-8")
    diagnoser = SkillDiagnoser(llm=MagicMock(), skills_dir=tmp_path / "skills")
    orch = SimpleNamespace(
        coding_runner=SimpleNamespace(diagnoser=diagnoser))

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    broker = ApprovalBroker()
    app = create_app(secret="phase2-secret", orchestrator=orch,
                     approval_broker=broker)
    app.state.session_factory = factory
    return TestClient(app), broker, skill_dir


def _skill_improve_payload(*, skill="reportgen", file_kind="SKILL.md",
                           patch="### 失败排查\n先查 stderr。",
                           decision="approve", owner="ou_1", open_id="ou_1",
                           iid="si1"):
    """构造 skill_improve 卡片回调 payload（suggestion 内嵌 JSON 字符串）。"""
    suggestion = json.dumps(
        {"skill": skill, "issue": "描述不清", "fix": "补充说明",
         "file": file_kind, "patch": patch}, ensure_ascii=False)
    return {"action": "skill_improve", "skill_improve_id": iid,
            "decision": decision, "owner": owner, "open_id": open_id,
            "suggestion": suggestion}


def test_skill_improve_owner_mismatch_forbidden(client_with_skill_diagnoser):
    """非发起者点击：forbidden，skill 文件不被改动。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(open_id="ou_other"))
    assert resp.json() == {"ok": False, "status": "forbidden"}
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "改进记录" not in md


def test_skill_improve_approve_applies_patch(client_with_skill_diagnoser):
    """owner 批准：diagnoser.apply 写回 SKILL.md（追加改进段落 + .bak 备份）。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload())
    body = resp.json()
    assert body["ok"] is True and body["status"] == "applied"
    assert body["file"].endswith("SKILL.md")
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## 改进记录" in md and "失败排查" in md
    assert (skill_dir / "SKILL.md.bak").exists()


def test_skill_improve_deny_does_not_apply(client_with_skill_diagnoser):
    """拒绝：决策落 broker，但不执行写回。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(decision="deny", iid="si2"))
    assert resp.json() == {"ok": True, "status": "decided", "decision": "deny"}
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "改进记录" not in md


def test_skill_improve_apply_failure_reported(client_with_skill_diagnoser):
    """apply 失败（skill 目录不存在）：返回 apply_failed + 错误原因。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(skill="ghost", iid="si3"))
    body = resp.json()
    assert body["ok"] is False and body["status"] == "apply_failed"
    assert "ghost" in body["reason"]


def test_skill_improve_duplicate_click_idempotent(client_with_skill_diagnoser):
    """重复点击：第二次 already_handled，改进段落只写一次。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    _post_card(client, _skill_improve_payload(iid="si4"))
    resp = _post_card(client, _skill_improve_payload(iid="si4"))
    assert resp.json()["status"] == "already_handled"
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert md.count("## 改进记录") == 1


def test_skill_improve_diagnoser_not_configured(client_with_broker):
    """orchestrator 无 diagnoser：批准已固化但返回 not configured 原因。"""
    client, broker = client_with_broker
    resp = _post_card(client, _skill_improve_payload(iid="si5"))
    body = resp.json()
    assert body["ok"] is False and body["status"] == "decided"
    assert body["reason"] == "skill diagnoser not configured"


# === Phase 28 后续：skill_improve 审计闭环 ===


def _audit_rows(client, limit: int = 20):
    """读测试 app 的 audit_logs（按时间倒序），返回 AuditLogRow 列表。"""
    from persistence.repositories.audit_repo import AuditRepo

    factory = client.app.state.session_factory
    s = factory()
    try:
        return AuditRepo(s).list_recent(limit=limit)
    finally:
        s.close()


def test_skill_improve_click_audited_with_target_id(client_with_skill_diagnoser):
    """F1：点击审计的 target_id 应为 skill_improve_id（原先恒为空，检索断链）。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(iid="siA"))
    assert resp.json()["status"] == "applied"
    rows = [r for r in _audit_rows(client) if r.action == "card_skill_improve"]
    assert rows and rows[0].target_id == "siA"


def test_code_approval_click_audited_with_target_id(client_with_broker):
    """F1 补遗：code_approval 点击审计 target_id 应为 code_approval_id。"""
    client, broker = client_with_broker
    resp = _post_card(client, {"action": "code_approval",
                               "code_approval_id": "caT", "decision": "approve",
                               "owner": "ou_1", "open_id": "ou_1"})
    assert resp.json()["status"] == "decided"
    rows = [r for r in _audit_rows(client) if r.action == "card_code_approval"]
    assert rows and rows[0].target_id == "caT"


def test_skill_improve_applied_audited(client_with_skill_diagnoser):
    """F2：apply 成功追加 system 审计，detail 含 file/backup（审计链不断在点击）。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(iid="siB"))
    assert resp.json()["status"] == "applied"
    rows = [r for r in _audit_rows(client) if r.action == "skill_improve_applied"]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_type == "system"
    assert row.target_id == "siB"
    assert row.detail_json.get("skill") == "reportgen"
    assert row.detail_json.get("file", "").endswith("SKILL.md")
    assert row.detail_json.get("backup", "").endswith("SKILL.md.bak")


def test_skill_improve_apply_failed_audited(client_with_skill_diagnoser):
    """F3：apply 失败也追加审计，detail 含失败原因。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(skill="ghost", iid="siC"))
    assert resp.json()["status"] == "apply_failed"
    rows = [r for r in _audit_rows(client) if r.action == "skill_improve_apply_failed"]
    assert len(rows) == 1
    row = rows[0]
    assert row.target_id == "siC"
    assert row.detail_json.get("skill") == "ghost"
    assert "ghost" in row.detail_json.get("reason", "")


def test_skill_improve_deny_has_no_apply_audit(client_with_skill_diagnoser):
    """deny：只有点击审计（decision=deny），无 applied/failed 记录。"""
    client, broker, skill_dir = client_with_skill_diagnoser
    resp = _post_card(client, _skill_improve_payload(decision="deny", iid="siD"))
    assert resp.json()["status"] == "decided"
    actions = [r.action for r in _audit_rows(client)]
    assert "card_skill_improve" in actions
    assert "skill_improve_applied" not in actions
    assert "skill_improve_apply_failed" not in actions
