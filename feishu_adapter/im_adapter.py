"""IM 消息发送：文本回复 + 卡片发送。

出站通道二选一（构造时注入）：
- sdk_client：lark-oapi SDK tenant 身份直连（生产/联调，ADR-0031 联调轮切换）
- cli      ：lark-cli 子进程（历史路径，保留供单测 mock）
"""
import json
from typing import Any

from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from feishu_adapter.client import LarkCLI, LarkCLIError


class IMAdapter:
    """对飞书 IM 消息发送的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None, sdk_client: Any = None) -> None:
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
        message_id: str = result.get("message_id", "")
        return message_id

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
        message_id: str = result.get("message_id", "")
        return message_id

    def upload_image(self, image_path: str) -> str:
        """上传本地图片 → image_key（Phase 20：sc 分析图 IM 回传）。

        POST /open-apis/im/v1/images（image_type=message）；仅 SDK 路径，
        CLI 路径抛 NotImplementedError（现部署均走 SDK）。
        """
        import lark_oapi as lark

        if self.sdk_client is None:
            raise NotImplementedError(
                "upload_image requires sdk_client")
        with open(image_path, "rb") as f:
            request = (lark.im.v1.CreateImageRequest.builder()
                       .request_body(
                           lark.im.v1.CreateImageRequestBody.builder()
                           .image_type("message")
                           .image(f)
                           .build())
                       .build())
            resp = self.sdk_client.im.v1.image.create(request)
        if not resp.success():
            raise LarkCLIError(
                f"image upload failed: code={resp.code} msg={resp.msg}")
        # 上传接口返回的是 image_key（非 image_id，真机 2026-09-01 验证）
        return resp.data.image_key or ""

    def send_image(self, chat_id: str, image_key: str) -> str:
        """按 image_key 发送图片消息到 chat_id（Phase 20：sc 分析图回传）。"""
        return self.send(chat_id, "chat_id", "image",
                         json.dumps({"image_key": image_key}))

    def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        """发送交互卡片。card 为简化结构 {header, elements}，返回新消息 ID。

        header 兼容两种形态：字符串标题（旧调用方）与完整 dict
        （{"title": {...}}，/model、/code 审批卡）。dict 形态直接透传——
        此前 str(dict) 会把 Python repr 渲染进卡片标题（ut-7 真机发现）。
        """
        card_json = self._card_json(card)
        if self.sdk_client is not None:
            return self._sdk_send(chat_id, "chat_id", "interactive", card_json)
        result = self.cli.run([
            "im", "message", "send",
            "--receive-id", chat_id,
            "--receive-id-type", "chat_id",
            "--msg-type", "interactive",
            "--content", card_json,
        ])
        message_id: str = result.get("message_id", "")
        return message_id

    def update_card(self, message_id: str, card: dict[str, Any]) -> None:
        """原地更新已发出的交互卡片（PATCH im/v1/messages/:message_id）。

        Phase 39：/code 进度卡 v2 原地刷新用（受理发一卡 → 节流刷新 →
        终态定格，不再刷节流文本）。仅 SDK 路径；CLI 抛
        NotImplementedError（调用方据此回退 v1 文本节流）。
        """
        if self.sdk_client is None:
            raise NotImplementedError("update_card requires sdk_client")
        body = (PatchMessageRequestBody.builder()
                .content(self._card_json(card))
                .build())
        request = (PatchMessageRequest.builder()
                   .message_id(message_id)
                   .request_body(body)
                   .build())
        resp = self.sdk_client.im.v1.message.patch(request)
        if not resp.success():
            raise LarkCLIError(
                f"im patch failed: code={resp.code} msg={resp.msg}")

    def download_message_file(self, message_id: str, file_key: str,
                              dest_path: Any) -> None:
        """下载消息中的文件附件到本地路径（Phase 77：/skill install zip 拉取）。

        GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=file；
        仅 SDK 路径（CLI 无对应子命令），失败抛 LarkCLIError。
        """
        import lark_oapi as lark

        if self.sdk_client is None:
            raise NotImplementedError(
                "download_message_file requires sdk_client")
        request = (lark.im.v1.GetMessageResourceRequest.builder()
                   .message_id(message_id)
                   .file_key(file_key)
                   .type("file")
                   .build())
        resp = self.sdk_client.im.v1.message_resource.get(request)
        if not resp.success():
            raise LarkCLIError(
                f"file download failed: code={resp.code} msg={resp.msg}")
        with open(dest_path, "wb") as f:
            f.write(resp.file.read())

    @staticmethod
    def _card_json(card: dict[str, Any]) -> str:
        """简化卡片结构 → 合法卡片 JSON 串（header 字符串/dict 双形态归一）。"""
        header = card.get("header", "")
        if not isinstance(header, dict):
            header = {"title": {"tag": "plain_text", "content": str(header)}}
        return json.dumps({
            "config": card.get("config") or {"wide_screen_mode": True},
            "header": header,
            "elements": card.get("elements", []),
        }, ensure_ascii=False)

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
