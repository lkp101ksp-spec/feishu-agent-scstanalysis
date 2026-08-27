"""IM 消息发送：文本回复 + 卡片发送。

出站通道二选一（构造时注入）：
- sdk_client：lark-oapi SDK tenant 身份直连（生产/联调，ADR-0031 联调轮切换）
- cli      ：lark-cli 子进程（历史路径，保留供单测 mock）
"""
import json

from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from feishu_adapter.client import LarkCLI, LarkCLIError


class IMAdapter:
    """对飞书 IM 消息发送的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None, sdk_client=None):
        self.cli = cli or LarkCLI()
        self.sdk_client = sdk_client

    def reply(self, chat_id: str, text: str) -> str:
        """向 chat_id 发送一条文本消息。返回新消息 ID。"""
        if self.sdk_client is not None:
            return self._sdk_send(chat_id, "chat_id", "text",
                                  json.dumps({"text": text}, ensure_ascii=False))
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", chat_id,
            "--receive-id-type", "chat_id",
            "--msg-type", "text",
            "--content", text,
        ])
        return result.get("message_id", "")

    def send(self, receive_id: str, receive_id_type: str, msg_type: str, content: str) -> str:
        """通用发送：支持 open_id / chat_id / email 等不同 receive_id_type。"""
        if self.sdk_client is not None:
            if msg_type == "text":
                content = self._ensure_text_json(content)
            return self._sdk_send(receive_id, receive_id_type, msg_type, content)
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", receive_id,
            "--receive-id-type", receive_id_type,
            "--msg-type", msg_type,
            "--content", content,
        ])
        return result.get("message_id", "")

    def send_card(self, chat_id: str, card: dict) -> str:
        """发送交互卡片。card 为简化结构 {header, elements}，此处补齐为合法卡片 JSON。"""
        card_json = json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": str(card.get("header", ""))}},
            "elements": card.get("elements", []),
        }, ensure_ascii=False)
        if self.sdk_client is not None:
            return self._sdk_send(chat_id, "chat_id", "interactive", card_json)
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", chat_id,
            "--receive-id-type", "chat_id",
            "--msg-type", "interactive",
            "--content", card_json,
        ])
        return result.get("message_id", "")

    @staticmethod
    def _ensure_text_json(content: str) -> str:
        """text 类型消息的 content 必须是 {"text": ...} JSON 串；裸文本则包装。"""
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "text" in parsed:
                return content
        except (ValueError, TypeError):
            pass
        return json.dumps({"text": content}, ensure_ascii=False)

    def _sdk_send(self, receive_id: str, receive_id_type: str,
                  msg_type: str, content: str) -> str:
        """走 lark-oapi SDK 发送消息（tenant 身份）。失败抛 LarkCLIError。"""
        body = (CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .build())
        request = (CreateMessageRequest.builder()
                   .receive_id_type(receive_id_type)
                   .request_body(body)
                   .build())
        resp = self.sdk_client.im.v1.message.create(request)
        if not resp.success():
            raise LarkCLIError(
                f"im send failed: code={resp.code} msg={resp.msg}")
        return resp.data.message_id or ""
