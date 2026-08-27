"""LLM Router：主备 OpenAI 兼容 API + 自动 fallback。

行为：
- 主模型：调用 max_retries + 1 次（含首次）
- 任一次主调用成功立即返回
- 主模型全部失败 → 调用备用 1 次
- 备用仍失败 → 抛 LLMCallError
- 统一剥离 reasoning 模型的 <think>…</think> content 前缀
  （Phase 10 联调实测：MiniMax-M3 将思考过程内联在 content 中）
"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from shared.errors import LLMCallError
from shared.schemas import ChatMessage

_THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)


def strip_think(content: str) -> str:
    """剥离 reasoning 模型内联的 <think> 块；无标签则原样返回。"""
    return _THINK_RE.sub("", content).strip()


@dataclass
class _Provider:
    base_url: str
    api_key: str
    model: str
    timeout_sec: int


class LLMRouter:
    """顺序调用主 → 备模型，全部失败抛 LLMCallError。"""

    def __init__(self, primary: dict, fallback: dict, max_retries: int = 1):
        self.primary = _Provider(**primary)
        self.fallback = _Provider(**fallback)
        self.max_retries = max_retries

    def _call_once(self, provider: _Provider, messages: list[dict]) -> dict:
        """调用一次 OpenAI 兼容 /chat/completions，返回完整 JSON 响应。"""
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": provider.model, "messages": messages}
        with httpx.Client(timeout=provider.timeout_sec) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def chat(self, messages: Iterable[ChatMessage]) -> str:
        """调用 LLM，返回 assistant content；主失败自动 fallback。"""
        msgs = [{"role": m.role, "content": m.content} for m in messages]
        last_err: Optional[Exception] = None

        # 主模型：(max_retries + 1) 次尝试
        for _ in range(self.max_retries + 1):
            try:
                data = self._call_once(self.primary, msgs)
                return strip_think(data["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError) as e:
                last_err = e

        # 备用模型：1 次
        try:
            data = self._call_once(self.fallback, msgs)
            return strip_think(data["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMCallError(
                f"primary failed ({last_err!r}), fallback failed ({e!r})"
            ) from e

    def call(self, *, role: str, prompt: str, tools: Optional[list] = None) -> str:
        """Phase 3：role-based 单轮 prompt 调用。

        Phase 3 简化版：role 仅作为 audit 标签；调用同 chat()。
        Phase 5 扩展：每个 role 独立 primary/fallback 模型配置。
        """
        return self.chat([
            ChatMessage(role="system", content=f"[role={role}]"),
            ChatMessage(role="user", content=prompt),
        ])
