"""Phase 44 场景级 provider 静态双绑（A1）。

/env CODE_PROVIDER、RESEARCH_PROVIDER 填 llm.yaml providers 池内的 name，
装配层为对应场景建独立 LLMRouter（primary=池条目，fallback=出厂 fallback）；
空/未命中 → None，调用方回退全局 router（/model 热切换语义不变）。
"""
from __future__ import annotations

import logging

from orchestrator.llm_router import LLMRouter

logger = logging.getLogger(__name__)


def build_scene_router(name: str, providers: dict, fallback_cfg: dict,
                       *, max_retries: int) -> LLMRouter | None:
    """按池内 name 建场景 router；空 name 或未命中 → None（回退全局）。

    providers：{name: ProviderCfg}（settings.llm.providers 转 dict）；
    fallback_cfg：出厂 fallback dict（base_url/api_key/model/timeout_sec）。
    """
    key = (name or "").strip()
    if not key:
        return None
    cfg = providers.get(key)
    if cfg is None:
        logger.warning("scene provider %r 不在候选池（缺 env 被跳过或拼写有误），"
                       "回退全局 router", key)
        return None
    return LLMRouter(
        primary={"base_url": cfg.base_url, "api_key": cfg.api_key,
                 "model": cfg.model, "timeout_sec": fallback_cfg["timeout_sec"]},
        fallback=dict(fallback_cfg),
        max_retries=max_retries,
    )
