"""/bind-doc 服务层：校验 + 写 session + 写 audit。

Phase 3：续期 / 续期卡片。
"""
import re
from datetime import datetime, timedelta, timezone

from orchestrator.session_service import SessionService
from persistence.repositories.audit_repo import AuditRepo
from shared.errors import BindDocInvalidError
from shared.ulid_ import new_ulid

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class BindDocService:
    """绑定飞书文档到当前会话。

    校验规则：
    - doc_id 非空
    - doc_id 匹配 `[A-Za-z0-9_-]{6,64}`

    Phase 3：
    - renew：续期当前 session 的 doc 绑定
    - maybe_send_renew_card：剩余有效期 ≤ 阈值时发续期卡片
    """

    def __init__(
        self,
        session_service: SessionService,
        audit_repo: AuditRepo,
        ttl_sec: int,
        session_repo=None,
        im_adapter=None,
        renew_threshold_sec: int = 300,
        doc_adapter=None,
    ):
        self.session_service = session_service
        self.audit_repo = audit_repo
        self.ttl_sec = ttl_sec
        self.session_repo = session_repo
        self.im_adapter = im_adapter
        self.renew_threshold_sec = renew_threshold_sec
        self.doc_adapter = doc_adapter
        # 续期卡片防重发：key = "session_id:expires_at"（续期/重绑后 key 变化，
        # 新一轮临期可再次提醒），value = 上次发卡时间
        self._renew_card_sent: dict[str, datetime] = {}

    def bind(self, session_id: str, owner_open_id: str, doc_id: str,
             anchor: str | None = None) -> datetime:
        """绑定 doc_id 到 session。返回过期时间。

        支持 "wiki:<token>" 前缀（normalizer 从 /wiki/ 链接提取）：
        经 doc_adapter.resolve_wiki_token 解析为真实 docx document_id。
        anchor：可选写入锚点文字，随绑定持久化到 session。
        """
        if doc_id.startswith("wiki:"):
            doc_id = self._resolve_wiki(doc_id[5:])
        if not doc_id or not _DOC_ID_RE.match(doc_id):
            raise BindDocInvalidError(f"invalid doc_id: {doc_id!r}")

        # 存在性探活（Phase 22）：doc_adapter 可用即校验——绑定时发现
        # 远早于写回时（P15 真机 nzb/nkb 一字之差 1770002 踩坑）
        if self.doc_adapter is not None:
            try:
                self.doc_adapter.list_root_children(doc_id)
            except Exception as e:
                raise BindDocInvalidError(
                    f"document not found: {doc_id}"
                    "（请检查 doc_id 或链接是否正确）") from e

        expires_at = self.session_service.bind_doc(
            session_id=session_id, doc_id=doc_id, ttl_sec=self.ttl_sec,
            anchor=anchor,
        )

        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="user",
            actor_id=owner_open_id,
            action="bind_doc",
            target_type="session",
            target_id=session_id,
            detail={"doc_id": doc_id, "expires_at": expires_at.isoformat(),
                    "anchor": anchor},
        )

        return expires_at

    def _resolve_wiki(self, wiki_token: str) -> str:
        """wiki token → docx document_id；通道缺失或解析失败转 BindDocInvalidError。"""
        if self.doc_adapter is None:
            raise BindDocInvalidError(
                "wiki 链接需要 DocAdapter（SDK 通道）支持，当前未配置")
        if not _DOC_ID_RE.match(wiki_token):
            raise BindDocInvalidError(f"invalid wiki token: {wiki_token!r}")
        try:
            obj_token = self.doc_adapter.resolve_wiki_token(wiki_token)
        except Exception as e:
            raise BindDocInvalidError(f"wiki 文档解析失败：{e}") from e
        if not obj_token:
            raise BindDocInvalidError("wiki 文档解析失败：空 obj_token")
        return obj_token

    # === Phase 3 ===
    def renew(self, *, session_id: str) -> datetime:
        """续期当前 session 的 bind。返回新 expires_at。

        new_expires_at = max(now + ttl_sec, current)  # 不会缩短
        """
        if self.session_repo is None:
            raise BindDocInvalidError("renew requires session_repo")
        sess = self.session_repo.get(session_id)
        if sess is None or not sess.bound_doc_id or not sess.bind_expires_at:
            raise BindDocInvalidError(f"session {session_id} has no active bind")
        now = datetime.now(timezone.utc)
        # 与 current 比较时统一 naive
        current = sess.bind_expires_at
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        candidate = now + timedelta(seconds=self.ttl_sec)
        new_exp = max(candidate, current)
        self.session_repo.update_bind_expires(
            session_id=session_id, bind_expires_at=new_exp
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="user", actor_id=session_id,
            action="renew_bind", target_type="session",
            target_id=session_id, detail={
                "old_expires": current.isoformat(),
                "new_expires": new_exp.isoformat(),
            },
        )
        return new_exp

    async def maybe_send_renew_card(self) -> None:
        """扫描所有 active session，剩余有效期 ≤ 阈值的发续期卡片。

        同一 (session, expires_at) 只发一次（内存去重，单进程 MVP；
        续期或重新绑定后 key 变化，下一轮临期会再提醒）。
        """
        if self.im_adapter is None or self.session_repo is None:
            return
        threshold = timedelta(seconds=self.renew_threshold_sec)
        now = datetime.now(timezone.utc)
        active = self.session_repo.list_active()
        for sess in active:
            if not sess.bound_doc_id or not sess.bind_expires_at:
                continue
            expires = sess.bind_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            remaining = expires - now
            if not (timedelta(0) < remaining <= threshold):
                continue
            card_key = f"{sess.session_id}:{expires.isoformat()}"
            if card_key in self._renew_card_sent:
                continue
            self._renew_card_sent[card_key] = now
            # 按钮文案随 TTL 配置（冒烟时常用短 TTL）
            ttl_text = (f"{self.ttl_sec // 60} 分钟" if self.ttl_sec >= 60
                        else f"{self.ttl_sec} 秒")
            self.im_adapter.send_card(
                chat_id=sess.source_chat_id,
                card={
                    "header": "bind_doc 即将过期",
                    "elements": [{
                        "tag": "action",
                        "actions": [{
                            "tag": "button",
                            "text": {"tag": "plain_text",
                                     "content": f"续期 {ttl_text}"},
                            "value": {"action": "renew_bind",
                                      "session_id": sess.session_id},
                        }],
                    }],
                },
            )
