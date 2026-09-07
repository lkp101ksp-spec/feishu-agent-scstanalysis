"""Phase 38 IntentGateService 单测：意图分类 / 确认卡 / 回调决策全分支。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.intent_gate import IntentGateService


def _incoming(text="对数据集 f1e89bf88edc 做双联体检测", **kw):
    """构造普通私聊 IncomingMessage 替身。"""
    return SimpleNamespace(
        text=text,
        chat_id=kw.get("chat_id", "oc_1"),
        sender_open_id=kw.get("sender_open_id", "ou_1"),
        message_id=kw.get("message_id", "om_1"),
        chat_type=kw.get("chat_type", "p2p"),
    )


@pytest.fixture
def gate():
    """LLM 判 research + 发卡成功的意图闸（可控时钟）。"""
    llm = MagicMock()
    llm.chat.return_value = '{"route": "research"}'
    im = MagicMock()
    clock = {"t": 1000.0}
    svc = IntentGateService(llm=llm, im=im, ttl_sec=1800,
                            now=lambda: clock["t"])
    svc._clock = clock  # 测试内拨时间
    return svc, llm, im


# ---------------------------------------------------------------- offer #


class TestMaybeOffer:
    def test_research_intent_sends_offer_card(self, gate):
        """研究意图：发确认卡并返回 intent_offered，不进闲聊路径。"""
        svc, llm, im = gate
        r = svc.maybe_offer(_incoming())
        assert r["status"] == "intent_offered" and r["intent_id"]
        assert r["route"] == "research"
        im.send_card.assert_called_once()
        card = im.send_card.call_args.args[1]
        assert card["header"]["title"]["content"] == "检测到研究任务意图"
        buttons = card["elements"][1]["actions"]
        values = [b["value"] for b in buttons]
        assert [v["action"] for v in values] == ["research_intent"] * 3
        # 三按钮：确认（分类路径）/ 纠偏（另一路径）/ 忽略
        assert [v["decision"] for v in values] == ["approve", "approve", "deny"]
        assert [v.get("route") for v in values] == ["research", "code", None]
        assert all(v["owner"] == "ou_1" for v in values)
        assert all(v["intent_id"] == r["intent_id"] for v in values)

    def test_code_intent_offer_card_route(self, gate):
        """代码意图：卡标题/主按钮走 /code，纠偏按钮给 /research。"""
        svc, llm, im = gate
        llm.chat.return_value = '{"route": "code"}'
        r = svc.maybe_offer(_incoming("帮我写个脚本统计文件行数"))
        assert r["route"] == "code"
        card = im.send_card.call_args.args[1]
        assert card["header"]["title"]["content"] == "检测到代码任务意图"
        values = [b["value"] for b in card["elements"][1]["actions"]]
        assert [v.get("route") for v in values] == ["code", "research", None]

    def test_chat_intent_falls_through(self, gate):
        """闲聊意图：返回 None 且不发卡。"""
        svc, llm, im = gate
        llm.chat.return_value = '{"route": "chat"}'
        assert svc.maybe_offer(_incoming("今天天气怎么样")) is None
        im.send_card.assert_not_called()

    def test_llm_error_falls_back_to_chat(self, gate):
        """分类调用异常：回退闲聊，绝不阻断正常聊天。"""
        svc, llm, im = gate
        llm.chat.side_effect = RuntimeError("llm down")
        assert svc.maybe_offer(_incoming()) is None
        im.send_card.assert_not_called()

    def test_bad_json_falls_back_to_chat(self, gate):
        """分类响应非 JSON：保守按闲聊处理。"""
        svc, llm, im = gate
        llm.chat.return_value = "我觉得是吧"
        assert svc.maybe_offer(_incoming()) is None
        im.send_card.assert_not_called()

    def test_unknown_route_falls_back_to_chat(self, gate):
        """分类返回未知路由值：按闲聊保守处理。"""
        svc, llm, im = gate
        llm.chat.return_value = '{"route": "whatever"}'
        assert svc.maybe_offer(_incoming()) is None
        im.send_card.assert_not_called()

    def test_slash_text_skipped_without_llm_call(self, gate):
        """/ 开头的未知指令：维持原闲聊行为，不消耗分类调用。"""
        svc, llm, im = gate
        assert svc.maybe_offer(_incoming("/foo bar")) is None
        llm.chat.assert_not_called()

    def test_send_card_failure_cleans_pending(self, gate):
        """发卡失败：清理挂起项并回退闲聊（不留死按钮）。"""
        svc, llm, im = gate
        im.send_card.side_effect = RuntimeError("im down")
        assert svc.maybe_offer(_incoming()) is None
        assert svc._pending == {}


# ---------------------------------------------------------------- decide #


class TestDecide:
    def _offer(self, svc):
        return svc.maybe_offer(_incoming())["intent_id"]

    def test_approve_returns_incoming_kwargs_and_card(self, gate):
        """确认执行：返回 /research 化的消息要素 + 原地换面卡。"""
        svc, llm, im = gate
        iid = self._offer(svc)
        r = svc.decide(iid, "approve", operator="ou_1", owner="ou_1",
                       route="research")
        assert r["ok"] and r["status"] == "intent_approved"
        assert r["route"] == "research"
        kw = r["incoming_kwargs"]
        assert kw["text"].startswith("/research ")
        assert "双联体检测" in kw["text"]
        assert kw["chat_id"] == "oc_1" and kw["sender_open_id"] == "ou_1"
        assert r["card"]["header"]["title"]["content"] == "研究任务已受理"
        # 换面卡不含按钮（防重复点击；服务端幂等另兜底）
        assert all(e["tag"] != "action" for e in r["card"]["elements"])

    def test_approve_with_route_override_switches_to_code(self, gate):
        """纠偏按钮：分类为 research 但用户点「改用 /code」→ 按 /code 受理。"""
        svc, llm, im = gate
        iid = self._offer(svc)
        r = svc.decide(iid, "approve", operator="ou_1", owner="ou_1",
                       route="code")
        assert r["status"] == "intent_approved" and r["route"] == "code"
        assert r["incoming_kwargs"]["text"].startswith("/code ")
        assert r["card"]["header"]["title"]["content"] == "代码任务已受理"

    def test_invalid_route_falls_back_to_classified(self, gate):
        """回调 route 非法：回退分类时的路由（不放任任意前缀注入）。"""
        svc, llm, im = gate
        iid = self._offer(svc)
        r = svc.decide(iid, "approve", operator="ou_1", owner="ou_1",
                       route="/etc/passwd")
        assert r["route"] == "research"
        assert r["incoming_kwargs"]["text"].startswith("/research ")

    def test_deny_returns_result_card(self, gate):
        svc, llm, im = gate
        iid = self._offer(svc)
        r = svc.decide(iid, "deny", operator="ou_1", owner="ou_1")
        assert r["ok"] and r["status"] == "intent_denied"
        assert r["card"]["header"]["title"]["content"] == "已忽略"

    def test_wrong_owner_forbidden(self, gate):
        """非发起者点击：forbidden，状态保持 pending（发起者仍可点）。"""
        svc, llm, im = gate
        iid = self._offer(svc)
        r = svc.decide(iid, "approve", operator="ou_other", owner="ou_1")
        assert r["status"] == "forbidden"
        assert svc._pending[iid]["status"] == "pending"

    def test_duplicate_click_already_handled(self, gate):
        svc, llm, im = gate
        iid = self._offer(svc)
        svc.decide(iid, "approve", operator="ou_1", owner="ou_1")
        r = svc.decide(iid, "approve", operator="ou_1", owner="ou_1")
        assert r["status"] == "already_handled"

    def test_unknown_id_expired(self, gate):
        svc, llm, im = gate
        r = svc.decide("ghost", "approve", operator="ou_1", owner="ou_1")
        assert r["status"] == "intent_expired"

    def test_ttl_expiry(self, gate):
        """超 TTL：按钮失效报 intent_expired（挂起项已清扫）。"""
        svc, llm, im = gate
        iid = self._offer(svc)
        svc._clock["t"] += 1801
        r = svc.decide(iid, "approve", operator="ou_1", owner="ou_1")
        assert r["status"] == "intent_expired"
        assert iid not in svc._pending
