"""意图预判闸（Phase 38）：普通私聊消息的研究意图识别 + 确认卡转 /research。

背景（ut-6 真 LLM 抽查发现）：普通自然语言消息走无工具闲聊路径，模型
会幻觉 <function_calls> 裸文本；用户不知道 /research 前缀就被当成闲聊。
本服务在 process() 闲聊路径前拦截：LLM 轻量分类 → 疑似研究意图发确认卡
（确认闸防误判白跑 Docker 任务）→ 用户点「确认执行」才转 ResearchRunner。

设计要点：
- 待确认意图存内存 dict（intent_id → 消息要素），TTL 过期失效；不落库
  ——与 code_approval 同级（会话级短生命周期，进程重启按钮即失效）。
- owner 内嵌卡片 value（回调比对不查库，同 code_approval 模式）。
- 单挂载点：只挂 orch.intent_gate，回调经 ctx.orchestrator 取同实例
  （ut-7 教训：出卡/回调双挂载点必须同源，此处从根上只有一处）。
- 分类调用失败/解析失败一律回退闲聊路径（绝不阻断正常聊天）。
"""
from __future__ import annotations

import logging
import threading
import time

from orchestrator.planner.planner import _extract_json_object
from shared.schemas import ChatMessage
from shared.ulid_ import new_ulid

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "判断用户消息该走哪条处理路径，三选一。\n"
    'research（研究/数据分析任务）：要求对数据集/文档执行分析、检测、计算、'
    "绘图、总结等具体操作，常含数据集引用（12 位 hex 编号）、生信工具名"
    "（sc_*/st_*）、分析名词（如双联体检测、细胞周期评分、富集分析、聚类、"
    "差异表达、细胞注释）。\n"
    "code（代码任务）：明确要求写代码、修 bug、重构、生成/解释脚本、"
    "操作本项目工作区文件。\n"
    "chat（闲聊/咨询）：问候、概念提问、用法咨询、情绪表达等。\n"
    '只输出 JSON：{"route": "research"} / {"route": "code"} / {"route": "chat"}'
)

_ROUTES = ("research", "code")
_ROUTE_LABEL = {"research": "/research", "code": "/code"}
_ROUTE_TITLE = {"research": "研究任务", "code": "代码任务"}

# 卡片正文指令预览长度上限（完整原文进内存 store，不受此限）
_PREVIEW_CAP = 200


def _offer_card(intent_id: str, owner: str, text: str, route: str) -> dict:
    """意图确认卡：指令预览 + 确认执行/改用另一路径/忽略（value 内嵌 owner）。

    三按钮（Feishu 单组上限 4）：主按钮按分类路径执行；纠偏按钮一键切换到
    另一条路（research↔code 误判时可不换卡片直接改道）；忽略回落闲聊。
    """
    preview = text[:_PREVIEW_CAP] + ("…" if len(text) > _PREVIEW_CAP else "")
    base = {"action": "research_intent", "intent_id": intent_id, "owner": owner}
    alt = "code" if route == "research" else "research"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content":
                             f"检测到{_ROUTE_TITLE[route]}意图"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"这条消息看起来像{_ROUTE_TITLE[route]}：\n> {preview}\n\n"
                f"确认后将作为 {_ROUTE_LABEL[route]} 任务受理"
                "（后台执行，完成自动回复）；"
                f"分类不对可改用 {_ROUTE_LABEL[alt]}；若是闲聊请点忽略。")}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content":
                          f"确认执行（{_ROUTE_LABEL[route]}）"},
                 "type": "primary",
                 "value": {**base, "decision": "approve", "route": route}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content":
                          f"改用 {_ROUTE_LABEL[alt]}"},
                 "type": "default",
                 "value": {**base, "decision": "approve", "route": alt}},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "忽略"},
                 "type": "default",
                 "value": {**base, "decision": "deny"}},
            ]},
        ],
    }


def _result_card(text: str, approved: bool, route: str = "research") -> dict:
    """点击后的原地换面卡（去按钮防重复点击；服务端幂等仍兜底）。"""
    preview = text[:_PREVIEW_CAP] + ("…" if len(text) > _PREVIEW_CAP else "")
    title, note = (
        (f"{_ROUTE_TITLE[route]}已受理",
         f"已按 {_ROUTE_LABEL[route]} 受理，正在执行，完成后自动回复。")
        if approved else ("已忽略", "该消息按普通聊天处理。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content":
                f"> {preview}\n\n{note}"}},
        ],
    }


class IntentGateService:
    """意图预判闸：maybe_offer（消息路径）+ decide（卡片回调路径）。"""

    def __init__(self, *, llm, im, ttl_sec: int = 1800,
                 now=lambda: time.time()) -> None:
        self.llm = llm
        self.im = im
        self.ttl_sec = ttl_sec
        self._now = now  # 纯函数式时间注入（测试可控，同 bio_workspace_gc 惯例）
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    # === 消息路径 ===

    def maybe_offer(self, incoming) -> dict | None:
        """私聊普通消息前置拦截：疑似研究意图发确认卡，否则返回 None 落闲聊。

        触发条件全部满足才发卡：服务所需依赖齐备、非 / 开头（未知指令维持
        原闲聊行为）、LLM 分类为研究意图。任何异常一律回退 None（不阻断聊天）。
        """
        text = (incoming.text or "").strip()
        if not text or text.startswith("/"):
            return None
        try:
            route = self._classify(text)
        except Exception:  # noqa: BLE001 —— 分类失败回退闲聊，绝不阻断
            logger.exception("intent classify failed (fallback to chat)")
            return None
        if route not in _ROUTES:
            return None
        intent_id = new_ulid()
        with self._lock:
            self._purge_expired()
            self._pending[intent_id] = {
                "text": text,
                "route": route,
                "chat_id": incoming.chat_id,
                "sender_open_id": incoming.sender_open_id,
                "message_id": incoming.message_id,
                "chat_type": getattr(incoming, "chat_type", "") or "p2p",
                "ts": self._now(),
                "status": "pending",
            }
        try:
            self.im.send_card(
                incoming.chat_id,
                _offer_card(intent_id, incoming.sender_open_id, text, route))
        except Exception:  # noqa: BLE001 —— 发卡失败清理挂起项并回退闲聊
            logger.exception("intent offer card send failed")
            with self._lock:
                self._pending.pop(intent_id, None)
            return None
        return {"status": "intent_offered", "intent_id": intent_id,
                "route": route}

    def _classify(self, text: str) -> str:
        """LLM 轻量分类：返回 research/code/chat；解析失败按 chat 保守处理。"""
        resp = self.llm.chat([
            ChatMessage(role="system", content=_CLASSIFY_SYSTEM),
            ChatMessage(role="user", content=text),
        ])
        route = str(_extract_json_object(resp).get("route", "chat")).strip()
        return route if route in _ROUTES else "chat"

    # === 回调路径 ===

    def decide(self, intent_id: str, decision: str, *,
               operator: str, owner: str = "", route: str = "") -> dict:
        """确认卡点击：owner 比对 + 内存幂等 + TTL 失效。

        route 为卡片 value 里的最终执行路径（用户可点纠偏按钮改道），
        非法值回退到分类时的 entry["route"]。
        返回 status：intent_approved（附 route/incoming_kwargs 与换面卡）/
        intent_denied（附换面卡）/ forbidden / already_handled / intent_expired。
        """
        with self._lock:
            self._purge_expired()
            entry = self._pending.get(intent_id)
            if entry is None:
                return {"ok": False, "status": "intent_expired"}
            if owner and operator and owner != operator:
                return {"ok": False, "status": "forbidden"}
            if entry["status"] != "pending":
                return {"ok": False, "status": "already_handled"}
            entry["status"] = "approved" if decision == "approve" else "denied"
        if decision != "approve":
            return {"ok": True, "status": "intent_denied",
                    "card": _result_card(entry["text"], approved=False)}
        final_route = route if route in _ROUTES else entry["route"]
        return {
            "ok": True,
            "status": "intent_approved",
            "route": final_route,
            "incoming_kwargs": {
                "message_id": entry["message_id"],
                "chat_id": entry["chat_id"],
                "sender_open_id": entry["sender_open_id"],
                "chat_type": entry["chat_type"],
                "text": f"{_ROUTE_LABEL[final_route]} " + entry["text"],
            },
            "card": _result_card(entry["text"], approved=True,
                                 route=final_route),
        }

    def _purge_expired(self) -> None:
        """清理过期挂起项（调用方须已持锁）。"""
        cutoff = self._now() - self.ttl_sec
        for k in [k for k, v in self._pending.items() if v["ts"] < cutoff]:
            del self._pending[k]
