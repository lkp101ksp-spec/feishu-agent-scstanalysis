"""ContextCompressor：80% 触发总结，95% 触发冻结。"""
from __future__ import annotations

from typing import Callable, Optional

from shared.errors import FreezeRequired
from shared.schemas import ChatMessage


class ContextCompressor:
    def __init__(
        self,
        *,
        llm_router,
        session_repo,
        audit_repo,
        token_counter: Optional[Callable] = None,
        token_budget: int = 200_000,
        compress_trigger_ratio: float = 0.8,
        freeze_trigger_ratio: float = 0.95,
        preserve_recent_n: int = 5,
    ) -> None:
        self.llm_router = llm_router
        self.session_repo = session_repo
        self.audit_repo = audit_repo
        self.token_counter = token_counter or self._default_counter
        self.token_budget = token_budget
        self.compress_trigger_ratio = compress_trigger_ratio
        self.freeze_trigger_ratio = freeze_trigger_ratio
        self.preserve_recent_n = preserve_recent_n

    def _default_counter(self, messages: list[ChatMessage]) -> int:
        return sum(len(m.content) // 4 for m in messages)

    def estimate_tokens(self, messages: list[ChatMessage]) -> int:
        return self.token_counter(messages)

    def maybe_compress(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """返回压缩后的 messages。超 95% 抛 FreezeRequired。"""
        tokens = self.estimate_tokens(messages)
        ratio = tokens / self.token_budget if self.token_budget else 0
        if ratio < self.compress_trigger_ratio:
            return messages
        # 80%+ 触发总结
        if len(messages) <= self.preserve_recent_n:
            if ratio >= self.freeze_trigger_ratio:
                raise FreezeRequired(
                    f"ratio {ratio:.2f} >= freeze {self.freeze_trigger_ratio}"
                )
            return messages
        old = messages[: -self.preserve_recent_n]
        recent = messages[-self.preserve_recent_n:]

        prompt = (
            f"将以下对话压缩到500 字以内：\n\n"
            + "\n".join(f"[{m.role}] {m.content}" for m in old)
        )
        summary_text = self.llm_router.call(role="context_compressor", prompt=prompt)

        new_messages = [
            ChatMessage(role="system", content=f"[已压缩] {summary_text}")
        ] + recent
        new_tokens = self.estimate_tokens(new_messages)
        new_ratio = new_tokens / self.token_budget if self.token_budget else 0
        if self.audit_repo is not None:
            self.audit_repo.write(
                actor_type="system", actor_id="context_compressor",
                action="compress_history", target_type="session",
                target_id="-", detail={
                    "saved_tokens": tokens - new_tokens,
                    "ratio_before": ratio,
                    "ratio_after": new_ratio,
                },
            )
        if new_ratio >= self.freeze_trigger_ratio:
            raise FreezeRequired(
                f"ratio after compress {new_ratio:.2f} >= freeze {self.freeze_trigger_ratio}"
            )
        return new_messages