"""bot 自身信息（评论事件防死循环的过滤依据，ADR-0033）。

SDK 1.7.3 未封装 bot info API（bot.v4 仅 SearchBot），按项目惯例用
BaseRequest 原始模式调 GET /open-apis/bot/v3/info。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark

logger = logging.getLogger(__name__)

_CACHE: dict[str, str | None] = {}


def get_bot_open_id(sdk_client: Any) -> str | None:
    """获取 bot open_id（进程级缓存）；失败返回 None（调用方保守跳过）。"""
    if "v" in _CACHE:
        return _CACHE["v"]
    try:
        req = (lark.BaseRequest.builder()
               .http_method(lark.HttpMethod.GET)
               .uri("/open-apis/bot/v3/info")
               .token_types({lark.AccessTokenType.TENANT})
               .build())
        resp = sdk_client.request(req)
        payload = json.loads(resp.raw.content)
        if payload.get("code") == 0:
            # 注意：本接口 bot 直接在顶层，无 data 包裹层（真机验证 2026-08-28）
            bot = payload.get("bot") or {}
            _CACHE["v"] = bot.get("open_id")
            return _CACHE["v"]
        logger.warning("bot info api failed: code=%s msg=%s",
                       payload.get("code"), payload.get("msg"))
    except Exception:
        logger.warning("get bot info failed", exc_info=True)
    _CACHE["v"] = None
    return None
