"""FastAPI 网关入口。

路由：
- GET  /health          : 健康检查
- POST /webhook/lark    : 飞书事件回调入口

完整流程：
1. 签名校验（基于原始 body）→ 401
2. 限流（按 app_id）→ 429
3. 归一化 webhook payload → IncomingMessage（400 if 非文本）
4. 幂等去重（idempotency_keys）→ 重投返回 200 duplicate
5. 调用 orchestrator.process(incoming)
6. 关联 task_id 到幂等键
"""
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, HTTPException, Request

from gateway.idempotency import build_idempotency_key
from gateway.normalizer import normalize_im_event, NormalizeError
from gateway.rate_limit import TokenBucket
from gateway.signature import verify_lark_signature
from persistence.engine import configure_engine
from persistence.repositories.idempotency_repo import IdempotencyRepo
from sqlalchemy.orm import sessionmaker
from shared.errors import (
    FeishuAgentError,
    RateLimitExceededError,
    SignatureInvalidError,
)

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """每个 App 实例的上下文（secret、限流、orchestrator）。"""
    secret: str
    rate: TokenBucket
    rate_per_min: int
    orchestrator: object  # Orchestrator 实例，类型在 Phase 1 避免循环引用
    bind_doc_service: object | None = None  # Phase 3：续期用
    template_service: object | None = None  # Phase 5：模板市场
    version_service: object | None = None  # Phase 6
    share_service: object | None = None  # Phase 6
    hot_loader: object | None = None  # Phase 6
    comment_service: object | None = None  # Phase 7
    search_service: object | None = None  # Phase 7
    public_service: object | None = None  # Phase 7
    fork_service: object | None = None  # Phase 7
    comment_sync_service: object | None = None  # Phase 8
    comment_action_service: object | None = None  # Phase 8
    diff_service: object | None = None  # Phase 8
    tag_service: object | None = None  # Phase 8
    favorite_service: object | None = None  # Phase 8
    unified_search_service: object | None = None  # Phase 9
    auto_sync_worker: object | None = None  # Phase 9


def create_app(
    secret: str,
    orchestrator,
    rate_per_min: int = 60,
    session_factory=None,
    bind_doc_service=None,
    template_service=None,
    version_service=None,
    share_service=None,
    hot_loader=None,
    comment_service=None,
    search_service=None,
    public_service=None,
    fork_service=None,
    comment_sync_service=None,
    comment_action_service=None,
    diff_service=None,
    tag_service=None,
    favorite_service=None,
    unified_search_service=None,
    auto_sync_worker=None,
) -> FastAPI:
    """工厂函数：创建并配置 FastAPI app。

    参数：
    - secret: 飞书 webhook 验签密钥
    - orchestrator: Orchestrator 实例（带 .process() 方法）
    - rate_per_min: 每分钟限流阈值
    - session_factory: 可调用对象，返回 Session；用于幂等表读写。
      不传则每次请求内即时从 persistence.engine.get_engine() 创建。
    """
    app = FastAPI(title="Feishu Research Agent — Phase 1")

    def _factory():
        if session_factory is not None:
            return session_factory()
        from persistence.engine import get_engine
        return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)()

    app.state.ctx = AppContext(
        secret=secret,
        rate=TokenBucket(capacity=rate_per_min, refill_per_sec=rate_per_min / 60.0),
        rate_per_min=rate_per_min,
        orchestrator=orchestrator,
        bind_doc_service=bind_doc_service,
        template_service=template_service,
        version_service=version_service,
        share_service=share_service,
        hot_loader=hot_loader,
        comment_service=comment_service,
        search_service=search_service,
        public_service=public_service,
        fork_service=fork_service,
        comment_sync_service=comment_sync_service,
        comment_action_service=comment_action_service,
        diff_service=diff_service,
        tag_service=tag_service,
        favorite_service=favorite_service,
        unified_search_service=unified_search_service,
        auto_sync_worker=auto_sync_worker,
    )

    # === Phase 9: 评论自动同步后台轮询（可选注入，ADR-0024）===
    if auto_sync_worker is not None:
        @app.on_event("startup")
        def _start_auto_sync_worker():
            auto_sync_worker.start_async()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # === Phase 2: 卡片回调入口 ===
    import json as _json
    from fastapi import HTTPException as _HTTPException
    from orchestrator.approval_service import ApprovalService

    card_secret = "phase2-dev-secret-change-me"

    @app.post("/webhook/lark/card")
    async def lark_card_webhook(request: Request):
        """Phase 2 卡片回调入口。

        流程：HMAC 验签 → 解析 body → 落 audit（Phase 2 简化版）。
        Phase 2.1 接入真实 approval_id → resolve_callback → Future resolve。
        """
        ctx: AppContext = app.state.ctx
        body_bytes = await request.body()
        signature = request.headers.get("X-Lark-Signature", "")
        svc = ApprovalService(secret=card_secret)
        if not svc.verify_callback(body_bytes, signature):
            logger.warning("card_callback bad_signature")
            raise _HTTPException(status_code=401, detail="bad signature")
        try:
            payload = _json.loads(body_bytes)
        except Exception as e:
            raise _HTTPException(status_code=400, detail=f"bad json: {e}")
        try:
            s = _factory()
            try:
                from persistence.repositories.audit_repo import AuditRepo
                from shared.ulid_ import new_ulid
                AuditRepo(s).write(
                    audit_id=new_ulid(),
                    actor_type="user",
                    actor_id=payload.get("open_id", ""),
                    action=f"card_{payload.get('action', 'unknown')}",
                    target_type="approval",
                    target_id=payload.get("approval_id", ""),
                    detail=payload,
                )
                s.commit()
            finally:
                s.close()
        except Exception:
            logger.exception("audit write failed (ignored)")
        # === Phase 3: renew_bind action ===
        action = payload.get("action", "")
        if action == "renew_bind":
            session_id = payload.get("session_id", "")
            bind_doc_service = getattr(ctx, "bind_doc_service", None)
            if bind_doc_service is None:
                logger.warning("renew_bind received but bind_doc_service not configured")
                return {"ok": False, "reason": "bind_doc_service not configured"}
            try:
                new_exp = bind_doc_service.renew(session_id=session_id)
                return {"ok": True, "new_expires": new_exp.isoformat()}
            except Exception as e:
                logger.exception("renew_bind failed")
                return {"ok": False, "reason": str(e)}
        return {"ok": True}

    # === Phase 5: 模板市场 API ===
    @app.post("/templates/block")
    async def create_block_template(request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        import json as _json
        from orchestrator.blocks.serializer import json_to_blocks
        blocks = json_to_blocks(_json.dumps(body["blocks"]))
        tid = ts.create_block(
            owner_open_id=body["owner_open_id"],
            name=body["name"],
            blocks=blocks,
            description=body.get("description", ""),
        )
        return {"ok": True, "template_id": tid}

    @app.post("/templates/subplan")
    async def create_subplan_template(request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        from orchestrator.templates.schemas import SubPlanTemplateStep
        steps = [SubPlanTemplateStep(**s) for s in body["steps"]]
        tid = ts.create_subplan(
            owner_open_id=body["owner_open_id"],
            name=body["name"],
            steps=steps,
            description=body.get("description", ""),
        )
        return {"ok": True, "template_id": tid}

    @app.get("/templates/")
    async def list_templates(owner_open_id: str):
        ctx = app.state.ctx
        if ctx.template_service is None:
            return {"templates": []}
        return {"templates": [
            t.template_id for t in ctx.template_service.list_by_owner(owner_open_id)
        ]}

    @app.post("/templates/{template_id}/render")
    async def render_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        params = body.get("params", {})
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "template_service not configured"}
        tpl = ts.get(template_id)
        if tpl is None:
            raise _HTTPException(status_code=404, detail="not found")
        if tpl.type == "block":
            blocks = ts.render_block(template_id=template_id, params=params)
            return {"ok": True, "blocks": [b.model_dump() for b in blocks]}
        else:
            steps = ts.render_subplan(template_id=template_id, params=params)
            return {"ok": True, "steps": [s.model_dump() for s in steps]}

    @app.delete("/templates/{template_id}")
    async def delete_template(template_id: str, caller_open_id: str):
        ctx = app.state.ctx
        ts = ctx.template_service
        if ts is None:
            return {"ok": False, "reason": "not configured"}
        try:
            ts.delete(template_id=template_id, caller_open_id=caller_open_id)
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    # === Phase 6: 版本 + 共享 + 热加载 ===
    @app.get("/templates/{template_id}/versions")
    async def list_versions(template_id: str):
        ctx = app.state.ctx
        vs = ctx.version_service
        if vs is None:
            return {"versions": []}
        return {"versions": vs.list_versions(template_id)}

    @app.post("/templates/{template_id}/rollback")
    async def rollback_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        vs = ctx.version_service
        if vs is None:
            raise _HTTPException(status_code=503, detail="version_service not configured")
        try:
            vs.rollback(template_id=template_id,
                        version_number=body["version_number"],
                        caller_open_id=body["caller_open_id"])
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.post("/templates/{template_id}/share")
    async def share_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ss = ctx.share_service
        if ss is None:
            raise _HTTPException(status_code=503, detail="share_service not configured")
        try:
            ss.share_to_chat(template_id=template_id,
                              chat_id=body["chat_id"],
                              caller_open_id=body["caller_open_id"])
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError:
            raise _HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.get("/templates/chat/{chat_id}")
    async def list_for_chat(chat_id: str):
        ctx = app.state.ctx
        ss = ctx.share_service
        if ss is None:
            return {"templates": []}
        out = []
        for t in ss.list_for_chat(chat_id):
            out.append(getattr(t, "template_id", t))
        return {"templates": out}

    @app.post("/admin/tools/upload")
    async def admin_upload_tool(request: Request):
        ctx = app.state.ctx
        body = await request.json()
        hl = ctx.hot_loader
        if hl is None:
            raise _HTTPException(status_code=503, detail="hot_loader not configured")
        from shared.errors import ToolBlockedError
        try:
            name = hl.upload(
                name=body["name"], code=body["code"],
                parameters=body["parameters"],
                risk_level=body["risk_level"],
                actor_open_id=body["actor_open_id"],
            )
        except ToolBlockedError as e:
            raise _HTTPException(status_code=400, detail=f"AST blocked: {e}")
        except ValueError as e:
            raise _HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "name": name}

    # === Phase 7: 评论 + 搜索 + 公共模板 + fork ===
    @app.get("/comments/{doc_id}")
    async def fetch_comments(doc_id: str, block_id: str | None = None):
        ctx = app.state.ctx
        cs = ctx.comment_service
        if cs is None:
            return {"text": "（comment_service 未配置）"}
        text = cs.fetch_thread(doc_id=doc_id, block_id=block_id)
        return {"text": text}

    @app.get("/templates/search")
    async def search_templates(
        q: str = "",
        scope: str | None = None,
        owner_open_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ):
        ctx = app.state.ctx
        ss = ctx.search_service
        if ss is None:
            return {"results": []}
        rows = ss.search(
            query=q, scope=scope, owner_open_id=owner_open_id,
            limit=limit, offset=offset,
        )
        return {"results": [
            getattr(r, "template_id", r) for r in rows
        ]}

    @app.post("/templates/{template_id}/submit-public")
    async def submit_public_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ps = ctx.public_service
        if ps is None:
            raise _HTTPException(status_code=503, detail="public_service not configured")
        try:
            ps.submit_for_review(
                template_id=template_id,
                actor_open_id=body["caller_open_id"],
            )
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError as e:
            raise _HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @app.post("/admin/templates/{template_id}/review")
    async def admin_review_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ps = ctx.public_service
        if ps is None:
            raise _HTTPException(status_code=503, detail="public_service not configured")
        action = body["action"]
        admin = body["admin_open_id"]
        try:
            if action == "approve":
                ps.approve(template_id=template_id, actor_open_id=admin,
                            note=body.get("note", ""))
            elif action == "reject":
                ps.reject(template_id=template_id, actor_open_id=admin,
                           reason=body.get("reason", ""))
            else:
                raise _HTTPException(status_code=400, detail="bad action")
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not admin")
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"ok": True}

    @app.get("/templates/public")
    async def list_public_templates():
        ctx = app.state.ctx
        ps = ctx.public_service
        if ps is None:
            return {"templates": []}
        return {"templates": [
            getattr(r, "template_id", r) for r in ps.list_public()
        ]}

    @app.post("/templates/{template_id}/fork")
    async def fork_template(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        fs = ctx.fork_service
        if fs is None:
            raise _HTTPException(status_code=503, detail="fork_service not configured")
        try:
            new_id = fs.fork_from_public(
                source_template_id=template_id,
                actor_open_id=body["caller_open_id"],
            )
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not public")
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "new_template_id": new_id}

    @app.get("/templates/{template_id}/forks")
    async def list_forks(template_id: str):
        ctx = app.state.ctx
        fs = ctx.fork_service
        if fs is None:
            return {"forks": []}
        return {"forks": [
            getattr(r, "template_id", r)
            for r in fs.list_forks(template_id)
        ]}

    # === Phase 8: 评论同步/动作 + diff + 标签/收藏 ===
    @app.post("/comments/{doc_id}/sync")
    async def sync_comments(doc_id: str):
        ctx = app.state.ctx
        cs = ctx.comment_sync_service
        if cs is None:
            raise _HTTPException(status_code=503, detail="comment_sync_service not configured")
        return cs.sync(doc_id=doc_id)

    @app.get("/comments/{doc_id}/stored")
    async def stored_comments(doc_id: str, block_id: str | None = None):
        ctx = app.state.ctx
        cs = ctx.comment_sync_service
        if cs is None:
            return {"comments": []}
        rows = cs.list_stored(doc_id=doc_id, block_id=block_id)
        return {"comments": [
            getattr(r, "comment_id", r) for r in rows
        ]}

    @app.post("/comments/{doc_id}/apply-actions")
    async def apply_comment_actions(doc_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ca = ctx.comment_action_service
        if ca is None:
            raise _HTTPException(status_code=503, detail="comment_action_service not configured")
        return ca.apply(doc_id=doc_id, caller_open_id=body["caller_open_id"])

    @app.get("/templates/{template_id}/diff")
    async def diff_template(template_id: str, v_a: int, v_b: int):
        ctx = app.state.ctx
        ds = ctx.diff_service
        if ds is None:
            raise _HTTPException(status_code=503, detail="diff_service not configured")
        try:
            return ds.diff(template_id=template_id, v_a=v_a, v_b=v_b)
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))

    @app.get("/templates/by-tag/{tag}")
    async def templates_by_tag(tag: str):
        ctx = app.state.ctx
        ts = ctx.tag_service
        if ts is None:
            return {"templates": []}
        return {"templates": [
            getattr(r, "template_id", r) for r in ts.find_by_tag(tag)
        ]}

    @app.get("/templates/favorites/{user_open_id}")
    async def favorites_of(user_open_id: str):
        ctx = app.state.ctx
        fs = ctx.favorite_service
        if fs is None:
            return {"templates": []}
        return {"templates": [
            getattr(r, "template_id", r) for r in fs.list_favorites(user_open_id)
        ]}

    @app.post("/templates/{template_id}/tags")
    async def add_tag(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        ts = ctx.tag_service
        if ts is None:
            raise _HTTPException(status_code=503, detail="tag_service not configured")
        try:
            normalized = ts.attach(
                template_id=template_id, tag=body["tag"],
                caller_open_id=body["caller_open_id"],
            )
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "tag": normalized}

    @app.delete("/templates/{template_id}/tags")
    async def remove_tag(template_id: str, tag: str, caller_open_id: str):
        ctx = app.state.ctx
        ts = ctx.tag_service
        if ts is None:
            raise _HTTPException(status_code=503, detail="tag_service not configured")
        try:
            ts.detach(template_id=template_id, tag=tag,
                       caller_open_id=caller_open_id)
        except PermissionError:
            raise _HTTPException(status_code=403, detail="not owner")
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"ok": True}

    @app.post("/templates/{template_id}/favorite")
    async def add_favorite(template_id: str, request: Request):
        ctx = app.state.ctx
        body = await request.json()
        fs = ctx.favorite_service
        if fs is None:
            raise _HTTPException(status_code=503, detail="favorite_service not configured")
        try:
            fs.favorite(template_id=template_id,
                         caller_open_id=body["caller_open_id"])
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"ok": True}

    @app.delete("/templates/{template_id}/favorite")
    async def remove_favorite(template_id: str, caller_open_id: str):
        ctx = app.state.ctx
        fs = ctx.favorite_service
        if fs is None:
            raise _HTTPException(status_code=503, detail="favorite_service not configured")
        fs.unfavorite(template_id=template_id, caller_open_id=caller_open_id)
        return {"ok": True}

    # === Phase 9: 融合检索 ===
    @app.get("/templates/search-v2")
    async def search_templates_v2(
        q: str = "",
        tag: str | None = None,
        scope: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ):
        ctx = app.state.ctx
        us = ctx.unified_search_service
        if us is None:
            return {"results": []}
        results = us.search(
            query=q, tag=tag, scope=scope, limit=limit, offset=offset,
        )
        return {"results": results}

    @app.post("/webhook/lark")
    async def lark_webhook(request: Request):
        ctx: AppContext = app.state.ctx
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")

        # 1. 签名校验
        ts = request.headers.get("X-Lark-Request-Timestamp", "")
        sig = request.headers.get("X-Lark-Signature", "")
        try:
            verify_lark_signature(timestamp=ts, body=body_str, signature=sig, secret=ctx.secret)
        except SignatureInvalidError as e:
            logger.warning("signature_invalid: %s", e)
            raise HTTPException(status_code=401, detail=str(e))

        # 2. 限流
        app_id = request.headers.get("X-Lark-App-Id", "default")
        try:
            ctx.rate.acquire(key=app_id)
        except RateLimitExceededError as e:
            logger.warning("rate_limited app=%s: %s", app_id, e)
            raise HTTPException(status_code=429, detail=str(e))

        # 3. 归一化
        try:
            payload = await request.json()
            incoming = normalize_im_event(payload)
        except NormalizeError as e:
            logger.warning("normalize_failed: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

        # 4. 幂等去重
        idem_key = build_idempotency_key(app_id, incoming.chat_id, incoming.message_id)
        s = _factory()
        try:
            idem_repo = IdempotencyRepo(s)
            if not idem_repo.try_reserve(idem_key):
                logger.info("duplicate webhook key=%s", idem_key)
                return {"status": "duplicate", "idempotency_key": idem_key}
            s.commit()  # 确保幂等行对后续请求可见
        finally:
            s.close()

        # 5. 业务处理
        try:
            result = ctx.orchestrator.process(incoming)
        except FeishuAgentError as e:
            logger.exception("orchestrator process failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

        # 6. 关联 task_id 到幂等键
        task_id = result.get("task_id") if isinstance(result, dict) else None
        if task_id:
            s = _factory()
            try:
                idem_repo = IdempotencyRepo(s)
                idem_repo.link_task(idem_key, task_id)
                s.commit()
            finally:
                s.close()

        return result

    return app