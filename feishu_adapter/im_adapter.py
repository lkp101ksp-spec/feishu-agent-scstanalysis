"""IM 消息发送：Phase 1 仅支持文本消息回复。"""

from feishu_adapter.client import LarkCLI


class IMAdapter:
    """对飞书 IM 消息发送的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None):
        self.cli = cli or LarkCLI()

    def reply(self, chat_id: str, text: str) -> str:
        """向 chat_id 发送一条文本消息。返回新消息 ID。"""
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
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", receive_id,
            "--receive-id-type", receive_id_type,
            "--msg-type", msg_type,
            "--content", content,
        ])
        return result.get("message_id", "")
