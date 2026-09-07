"""Phase 44 场景级 provider 静态双绑（A1）：build_scene_router 纯函数 +
settings env 加载 + Orchestrator/ResearchRunner 接线。

分工语义：/code 与 /research 可各自绑定 llm.yaml providers 池内条目
（env CODE_PROVIDER / RESEARCH_PROVIDER），空 = 跟随全局 router（/model
热切换继续管闲聊/意图闸等其余场景）。
"""
import logging
from unittest.mock import MagicMock

from config.settings import ProviderCfg
from orchestrator.llm_router import LLMRouter
from orchestrator.scene_router import build_scene_router

_POOL = {
    "kimi": ProviderCfg(name="kimi", base_url="https://kimi.example/v1",
                        api_key="sk-kimi", model="k3"),
    "glm": ProviderCfg(name="glm", base_url="https://glm.example/v1",
                       api_key="sk-glm", model="glm-5.3"),
}
_FALLBACK = {"base_url": "https://fb.example/v1", "api_key": "sk-fb",
             "model": "fb-model", "timeout_sec": 30}


class TestBuildSceneRouter:
    def test_empty_name_returns_none(self):
        """空/空白 name → None（调用方回退全局 router）。"""
        assert build_scene_router("", _POOL, _FALLBACK, max_retries=1) is None
        assert build_scene_router("   ", _POOL, _FALLBACK, max_retries=1) is None

    def test_hit_builds_router_with_pool_primary(self):
        """命中池条目：primary=池配置，fallback=出厂 fallback，max_retries 透传。"""
        r = build_scene_router("kimi", _POOL, _FALLBACK, max_retries=1)
        assert isinstance(r, LLMRouter)
        assert r.primary.base_url == "https://kimi.example/v1"
        assert r.primary.api_key == "sk-kimi"
        assert r.primary.model == "k3"
        assert r.fallback.base_url == "https://fb.example/v1"
        assert r.fallback.model == "fb-model"
        assert r.max_retries == 1

    def test_miss_returns_none_and_warns(self, caplog):
        """name 不在池（缺 env 被跳过/拼错）：回退 None + 告警，不阻塞启动。"""
        with caplog.at_level(logging.WARNING):
            r = build_scene_router("nope", _POOL, _FALLBACK, max_retries=1)
        assert r is None
        assert any("nope" in rec.message for rec in caplog.records)


class TestSettingsEnvLoad:
    def test_code_and_research_provider_env(self, monkeypatch):
        """CODE_PROVIDER / RESEARCH_PROVIDER 环境变量进入 settings（默认空）。"""
        import config.settings as settings_mod
        from config.settings import load_settings
        # 隔离真实 .env：部署环境含 CODE_PROVIDER 时 load_env_file 会 setdefault 回注
        monkeypatch.setattr(settings_mod, "load_env_file", lambda path=".env": None)
        for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK_SECRET",
                    "LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
                    "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL"):
            monkeypatch.setenv(key, f"t-{key.lower()}")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///t.db")
        monkeypatch.delenv("CODE_PROVIDER", raising=False)
        monkeypatch.delenv("RESEARCH_PROVIDER", raising=False)
        s = load_settings()
        assert s.code_provider == "" and s.research_provider == ""
        monkeypatch.setenv("CODE_PROVIDER", "kimi")
        monkeypatch.setenv("RESEARCH_PROVIDER", "primary")
        s = load_settings()
        assert s.code_provider == "kimi" and s.research_provider == "primary"


class TestOrchestratorWiring:
    """Orchestrator(research_llm=...)：research_llm 属性接线与回退。
    （planner 创建在 settings+doc_adapter 齐全的重分支内，其
    Planner(llm_router=self.research_llm) 接线由集成回归覆盖。）"""

    @staticmethod
    def _orch(llm, research_llm=None):
        from orchestrator.app import Orchestrator
        kwargs = {}
        if research_llm is not None:
            kwargs["research_llm"] = research_llm
        return Orchestrator(llm, MagicMock(), MagicMock(), MagicMock(),
                            MagicMock(), MagicMock(), **kwargs)

    def test_default_research_llm_follows_global(self):
        global_llm = MagicMock(name="global")
        orch = self._orch(global_llm)
        assert orch.llm is global_llm
        assert orch.research_llm is global_llm

    def test_scene_research_llm_wired(self):
        global_llm = MagicMock(name="global")
        scene = MagicMock(name="scene")
        orch = self._orch(global_llm, research_llm=scene)
        assert orch.llm is global_llm            # 闲聊/意图闸保持全局
        assert orch.research_llm is scene


class TestResearchLlmHelper:
    """research_runner._research_llm：research_llm 优先，回退 orch.llm。"""

    @staticmethod
    def _orch(**kw):
        from types import SimpleNamespace
        return SimpleNamespace(**kw)

    def test_prefers_research_llm(self):
        from orchestrator.research_runner import _research_llm
        o = self._orch(llm="g", research_llm="s")
        assert _research_llm(o) == "s"

    def test_falls_back_to_llm(self):
        from orchestrator.research_runner import _research_llm
        o = self._orch(llm="g")
        assert _research_llm(o) == "g"

    def test_none_research_llm_falls_back(self):
        from orchestrator.research_runner import _research_llm
        o = self._orch(llm="g", research_llm=None)
        assert _research_llm(o) == "g"
