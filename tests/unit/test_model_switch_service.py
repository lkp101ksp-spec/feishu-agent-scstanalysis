"""Phase 30 ModelSwitchService 单测：admin 门禁 / 热切换 / 状态卡 key 不回显 / 启动恢复。"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config.settings import ProviderCfg, _parse_providers
from orchestrator.llm_router import LLMRouter
from orchestrator.model_switch_service import ModelSwitchService
from persistence.models import Base
from persistence.repositories.llm_active_repo import LLMActiveRepo

ADMIN = "ou_admin"
OTHER = "ou_other"


def _router() -> LLMRouter:
    return LLMRouter(
        primary={"base_url": "https://a.example.com/v1", "api_key": "sk-primary",
                 "model": "model-a", "timeout_sec": 30},
        fallback={"base_url": "https://b.example.com/v1", "api_key": "sk-fallback",
                  "model": "model-b", "timeout_sec": 30},
        max_retries=1,
    )


def _providers() -> tuple[ProviderCfg, ...]:
    return (
        ProviderCfg(name="a", base_url="https://a.example.com/v1",
                    api_key="sk-primary", model="model-a"),
        ProviderCfg(name="b", base_url="https://b.example.com/v1",
                    api_key="sk-fallback", model="model-b"),
        ProviderCfg(name="kimi", base_url="https://k.example.com/v1",
                    api_key="sk-kimi", model="kimi-x"),
    )


@pytest.fixture
def svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    service = ModelSwitchService(
        llm=_router(), providers=_providers(),
        admin_ids={ADMIN}, session_factory=factory,
    )
    service._engine = engine  # 测试内直查 DB
    yield service


# ---------------------------------------------------------------- switch #


class TestSwitch:
    def test_admin_switch_primary_reconfigures_and_persists(self, svc):
        """admin 切主：路由实例原地换 Provider + llm_active 落行。"""
        r = svc.switch(ADMIN, "kimi", "primary")
        assert r["ok"] and r["slot"] == "primary" and r["to"] == "kimi"
        assert r["from"] == "a"
        assert svc.llm.primary.model == "kimi-x"
        assert svc.llm.primary.api_key == "sk-kimi"
        assert svc.llm.fallback.model == "model-b"  # 备保持
        s = svc.session_factory()
        try:
            row = LLMActiveRepo(s).get()
            assert (row.primary_name, row.fallback_name) == ("kimi", "b")
        finally:
            s.close()

    def test_switch_fallback(self, svc):
        """切备：主保持、备替换。"""
        r = svc.switch(ADMIN, "kimi", "fallback")
        assert r["ok"] and r["from"] == "b"
        assert svc.llm.fallback.model == "kimi-x"
        assert svc.llm.primary.model == "model-a"

    def test_same_candidate_both_slots(self, svc):
        """同一候选先后设主设备：允许（自由度给管理员）。"""
        assert svc.switch(ADMIN, "kimi", "primary")["ok"]
        r = svc.switch(ADMIN, "kimi", "fallback")
        assert r["ok"]
        assert svc.llm.primary.model == "kimi-x"
        assert svc.llm.fallback.model == "kimi-x"
        s = svc.session_factory()
        try:
            row = LLMActiveRepo(s).get()
            assert (row.primary_name, row.fallback_name) == ("kimi", "kimi")
        finally:
            s.close()

    def test_non_admin_forbidden(self, svc):
        """非 admin：拒绝且不落库不换路由。"""
        r = svc.switch(OTHER, "kimi", "primary")
        assert r == {"ok": False, "reason": "forbidden"}
        assert svc.llm.primary.model == "model-a"
        s = svc.session_factory()
        try:
            assert LLMActiveRepo(s).get() is None
        finally:
            s.close()

    def test_empty_admin_ids_denies_all(self, svc):
        """空名单 = 无人可切（安全默认）。"""
        svc.admin_ids = set()
        assert svc.switch(ADMIN, "kimi", "primary")["reason"] == "forbidden"

    def test_unknown_provider(self, svc):
        """幻觉候选名（不在 providers）：拒绝。"""
        r = svc.switch(ADMIN, "nope", "primary")
        assert r == {"ok": False, "reason": "unknown_provider"}

    def test_bad_slot(self, svc):
        """非法槽位：拒绝。"""
        r = svc.switch(ADMIN, "kimi", "middle")
        assert r == {"ok": False, "reason": "bad_slot"}


# ------------------------------------------------------------ status card #


class TestStatusCard:
    def test_non_admin_gets_none(self, svc):
        assert svc.status_card(OTHER) is None

    def test_admin_card_never_contains_api_key(self, svc):
        """安全铁律：卡片全 JSON 序列化结果不含任何 api key 明文。"""
        svc.switch(ADMIN, "kimi", "primary")
        blob = json.dumps(svc.status_card(ADMIN), ensure_ascii=False)
        for key in ("sk-primary", "sk-fallback", "sk-kimi"):
            assert key not in blob
        assert "api_key" not in blob

    def test_card_shape_and_current_markers(self, svc):
        """卡 1（双卡片流程）：状态行 + 两个槽位入口按钮（model_pick）。"""
        card = svc.status_card(ADMIN)
        assert card["header"]["title"]["content"].startswith("/model")
        btns = [b for el in card["elements"] if el.get("tag") == "action"
                for b in el["actions"]]
        values = [b["value"] for b in btns]
        assert values == [
            {"action": "model_pick", "slot": "primary"},
            {"action": "model_pick", "slot": "fallback"},
        ]
        # 卡 1 不再直接放模型切换按钮（选模型在卡 2）
        assert not [v for v in values if v["action"] == "model_switch"]

    def test_empty_providers_shows_hint(self, svc):
        """候选池为空：提示配置路径，无按钮。"""
        svc.providers = {}
        card = svc.status_card(ADMIN)
        assert not [el for el in card["elements"] if el.get("tag") == "action"]
        blob = json.dumps(card, ensure_ascii=False)
        assert "providers" in blob


# ------------------------------------------------------------ picker card #


class TestPickerCard:
    def test_non_admin_gets_none(self, svc):
        assert svc.picker_card(OTHER, "primary") is None

    def test_bad_slot_gets_none(self, svc):
        assert svc.picker_card(ADMIN, "middle") is None

    def test_lists_all_providers_with_switch_values(self, svc):
        """卡 2：候选池全部模型各一个 model_switch 按钮 + 返回按钮。"""
        card = svc.picker_card(ADMIN, "primary")
        assert "选择主模型" in card["header"]["title"]["content"]
        btns = [b for el in card["elements"] if el.get("tag") == "action"
                for b in el["actions"]]
        values = [b["value"] for b in btns]
        for name in ("a", "b", "kimi"):
            assert {"action": "model_switch", "name": name,
                    "slot": "primary"} in values
        assert {"action": "model_back"} in values
        # 当前主 a：禁用 + ✓
        cur = next(b for b in btns if b["value"].get("name") == "a")
        assert cur["disabled"] is True and "✓" in cur["text"]["content"]

    def test_fallback_slot_marks_current_fallback(self, svc):
        """备槽位卡：✓ 标当前备 b 而非主 a。"""
        card = svc.picker_card(ADMIN, "fallback")
        btns = [b for el in card["elements"] if el.get("tag") == "action"
                for b in el["actions"]]
        cur = next(b for b in btns if b["value"].get("name") == "b")
        assert cur["disabled"] is True and "✓" in cur["text"]["content"]
        other = next(b for b in btns if b["value"].get("name") == "a")
        assert other["disabled"] is False

    def test_picker_never_contains_api_key(self, svc):
        blob = json.dumps(svc.picker_card(ADMIN, "primary"), ensure_ascii=False)
        for key in ("sk-primary", "sk-fallback", "sk-kimi"):
            assert key not in blob
        assert "api_key" not in blob


# -------------------------------------------------------------- startup #


class TestApplyStartup:
    def test_restores_from_db(self, svc):
        """切换过 → 新实例启动按 DB 恢复主备。"""
        svc.switch(ADMIN, "kimi", "primary")
        fresh = ModelSwitchService(
            llm=_router(), providers=_providers(),
            admin_ids={ADMIN}, session_factory=svc.session_factory)
        fresh.apply_startup()
        assert fresh.llm.primary.model == "kimi-x"
        assert fresh.llm.fallback.model == "model-b"

    def test_stale_name_falls_back_to_default(self, svc):
        """DB 名字已不在候选池（yaml 改过）：回退默认不动。"""
        s = svc.session_factory()
        try:
            LLMActiveRepo(s).upsert("ghost", "b")
            s.commit()
        finally:
            s.close()
        svc.apply_startup()
        assert svc.llm.primary.model == "model-a"

    def test_empty_table_noop(self, svc):
        svc.apply_startup()
        assert svc.llm.primary.model == "model-a"


# ------------------------------------------------------- config parsing #


class TestParseProviders:
    def test_missing_env_skipped(self, monkeypatch):
        """缺 env 的条目静默跳过，其余正常解析。"""
        monkeypatch.setenv("P_A_URL", "https://a/v1")
        monkeypatch.setenv("P_A_KEY", "sk-1")
        monkeypatch.setenv("P_A_MODEL", "m1")
        monkeypatch.delenv("P_B_URL", raising=False)
        cfg = {"providers": [
            {"name": "a", "base_url_env": "P_A_URL",
             "api_key_env": "P_A_KEY", "model_env": "P_A_MODEL"},
            {"name": "b", "base_url_env": "P_B_URL",
             "api_key_env": "P_B_KEY", "model_env": "P_B_MODEL"},
        ]}
        got = _parse_providers(cfg)
        assert [p.name for p in got] == ["a"]

    def test_absent_section_empty(self):
        assert _parse_providers({}) == ()


# ------------------------------------------------------------- router #


class TestReconfigure:
    def test_same_instance_shared_refs_see_change(self):
        """reconfigure 后共享同一 router 引用的调用方立即看到新模型。"""
        r = _router()
        holder = {"llm": r}  # 模拟 coding_runner 等持有点
        r.reconfigure(
            {"base_url": "https://c.example.com/v1", "api_key": "sk-c",
             "model": "model-c", "timeout_sec": 30},
            {"base_url": "https://d.example.com/v1", "api_key": "sk-d",
             "model": "model-d", "timeout_sec": 30},
        )
        assert holder["llm"].primary.model == "model-c"
        assert holder["llm"].fallback.model == "model-d"
        assert r.max_retries == 1  # 缺省不变
