"""Phase 30 可视化模型切换：候选池状态卡 + 热切换（admin 限定，key 不回显）。

设计（docs/superpowers/plans/2026-09-04-phase30-model-switch.md）：
- 候选池来自 config/llm.yaml providers 段（key 只在 .env/内存）
- active 状态存 llm_active 单行表（只存名字）；空表回退 yaml 默认主备
- 热切换 = LLMRouter.reconfigure 原地替换（全链路共享引用立即生效）
"""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.orm import sessionmaker

from config.settings import ProviderCfg
from orchestrator.llm_router import LLMRouter
from persistence.repositories.llm_active_repo import LLMActiveRepo

_SLOTS = ("primary", "fallback")


def _host(base_url: str) -> str:
    """base_url → 展示用 host（卡片上不出现完整 URL，更不出现 key）。"""
    try:
        return urlparse(base_url).netloc or base_url
    except ValueError:
        return base_url


class ModelSwitchService:
    """/model 命令与 model_switch 卡片回调的公共业务层。"""

    def __init__(self, *, llm: LLMRouter, providers: tuple[ProviderCfg, ...],
                 admin_ids: set[str],
                 session_factory: sessionmaker) -> None:
        self.llm = llm
        self.providers: dict[str, ProviderCfg] = {p.name: p for p in providers}
        self.admin_ids = admin_ids
        self.session_factory = session_factory

    # ------------------------------------------------------------------ #
    def is_admin(self, open_id: str) -> bool:
        """admin 名单校验（空名单 = 无人可切，安全默认）。"""
        return bool(open_id) and open_id in self.admin_ids

    def _current(self) -> tuple[str, str]:
        """当前主备候选名：DB 有记录用之，否则按路由实际值反查名字。"""
        s = self.session_factory()
        try:
            row = LLMActiveRepo(s).get()
            if row is not None:
                return row.primary_name, row.fallback_name
        finally:
            s.close()
        return (self._name_of(self.llm.primary),
                self._name_of(self.llm.fallback))

    def _name_of(self, provider) -> str:
        """按 model+host 在候选池反查名字；查不到返回描述形态。"""
        host = _host(provider.base_url)
        for name, cfg in self.providers.items():
            if cfg.model == provider.model and _host(cfg.base_url) == host:
                return name
        return f"{provider.model}@{host}"

    # ------------------------------------------------------------------ #
    def status_card(self, open_id: str) -> dict | None:
        """admin 查看状态卡；非 admin 返回 None（调用方回文本提示）。"""
        if not self.is_admin(open_id):
            return None
        cur_p, cur_f = self._current()
        lines = [
            f"主模型：{self._display(cur_p, True)}",
            f"备模型：{self._display(cur_f, False)}",
        ]
        elements: list[dict] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
        ]
        if not self.providers:
            elements.append({"tag": "div", "text": {"tag": "lark_md",
                "content": "候选池为空：在 config/llm.yaml providers 段添加候选"
                           "（并在 .env 配置对应变量）后可切换。"}})
        else:
            buttons = []
            for name in self.providers:
                buttons.append({
                    "tag": "button",
                    "text": {"tag": "plain_text",
                             "content": f"{name}→主" + (" ✓" if name == cur_p else "")},
                    "type": "primary" if name != cur_p else "default",
                    "disabled": name == cur_p,
                    "value": {"action": "model_switch", "name": name,
                              "slot": "primary"},
                })
                buttons.append({
                    "tag": "button",
                    "text": {"tag": "plain_text",
                             "content": f"{name}→备" + (" ✓" if name == cur_f else "")},
                    "type": "default",
                    "disabled": name == cur_f,
                    "value": {"action": "model_switch", "name": name,
                              "slot": "fallback"},
                })
            elements.append({"tag": "action", "actions": buttons})
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text",
             "content": "点击按钮热切换（即时生效，无需重启）；api key 不显示"}]})
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": "/model 模型切换（管理员）"}},
            "elements": elements,
        }

    def _display(self, name: str, is_primary: bool) -> str:
        """槽位展示文案：候选名 · 模型 · host（不含 key）。"""
        cfg = self.providers.get(name)
        if cfg is not None:
            return f"**{cfg.name}** · {cfg.model} · {_host(cfg.base_url)}"
        p = self.llm.primary if is_primary else self.llm.fallback
        return f"**{name}** · {p.model} · {_host(p.base_url)}"

    # ------------------------------------------------------------------ #
    def apply_startup(self) -> None:
        """启动时应用 DB 记忆的主备（覆盖 yaml 默认）。

        名字已不在候选池（yaml 改过）或空表时回退默认，不抛异常。
        """
        s = self.session_factory()
        try:
            row = LLMActiveRepo(s).get()
        finally:
            s.close()
        if row is None:
            return
        pcfg = self.providers.get(row.primary_name)
        fcfg = self.providers.get(row.fallback_name)
        if pcfg is None or fcfg is None:
            return
        self.llm.reconfigure(
            {"base_url": pcfg.base_url, "api_key": pcfg.api_key,
             "model": pcfg.model, "timeout_sec": self.llm.primary.timeout_sec},
            {"base_url": fcfg.base_url, "api_key": fcfg.api_key,
             "model": fcfg.model, "timeout_sec": self.llm.fallback.timeout_sec},
        )

    def switch(self, open_id: str, name: str, slot: str) -> dict:
        """热切换指定槽位到候选 name：校验 → reconfigure → DB 持久化。"""
        if not self.is_admin(open_id):
            return {"ok": False, "reason": "forbidden"}
        if slot not in _SLOTS:
            return {"ok": False, "reason": "bad_slot"}
        cfg = self.providers.get(name)
        if cfg is None:
            return {"ok": False, "reason": "unknown_provider"}

        cur_p, cur_f = self._current()
        # 另一槽保持现状：名字能解析则用候选定义，否则沿用路由当前实例值
        other_name = cur_f if slot == "primary" else cur_p
        other_cfg = self.providers.get(other_name)
        cur = self.llm.primary if slot == "primary" else self.llm.fallback
        other = self.llm.fallback if slot == "primary" else self.llm.primary
        new_cfg_dict = {"base_url": cfg.base_url, "api_key": cfg.api_key,
                        "model": cfg.model, "timeout_sec": cur.timeout_sec}
        other_dict = ({"base_url": other_cfg.base_url, "api_key": other_cfg.api_key,
                       "model": other_cfg.model, "timeout_sec": other.timeout_sec}
                      if other_cfg is not None else
                      {"base_url": other.base_url, "api_key": other.api_key,
                       "model": other.model, "timeout_sec": other.timeout_sec})

        from_name = cur_p if slot == "primary" else cur_f
        if slot == "primary":
            self.llm.reconfigure(new_cfg_dict, other_dict)
            new_p_name, new_f_name = name, other_name
        else:
            self.llm.reconfigure(other_dict, new_cfg_dict)
            new_p_name, new_f_name = other_name, name

        s = self.session_factory()
        try:
            LLMActiveRepo(s).upsert(new_p_name, new_f_name)
            s.commit()
        finally:
            s.close()
        return {"ok": True, "slot": slot, "from": from_name, "to": name}
