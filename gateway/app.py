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


def create_app(
    secret: str,
    orchestrator,
    rate_per_min: int = 60,
    session_factory=None,
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
    )

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
        return {"ok": True}

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