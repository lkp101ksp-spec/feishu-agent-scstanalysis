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
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.orm import sessionmaker

from gateway.idempotency import build_idempotency_key
from gateway.normalizer import NormalizeError, normalize_im_event
from gateway.rate_limit import TokenBucket
from gateway.signature import verify_lark_signature
from persistence.repositories.idempotency_repo import IdempotencyRepo
from shared.errors import (
    FeishuAgentError,
    RateLimitExceededError,
    SignatureInvalidError,
)

logger = logging.getLogger(__name__)


def run_im_pipeline(app: FastAPI, app_id: str, payload: dict) -> dict:
    """IM 事件公共管线（ADR-0031）：限流 → 归一化 → 幂等 → process → link_task。

    webhook 路由（验签后）与 ws 长连接进程共用本函数，保证行为一致；
    业务异常（RateLimitExceededError/NormalizeError/FeishuAgentError）透传给
    调用方，由各自入口转译（HTTP 状态码 / ws 日志）。
    """
    ctx: AppContext = app.state.ctx
    # 1. 限流
    try:
        ctx.rate.acquire(key=app_id)
    except RateLimitExceededError as e:
        logger.warning("rate_limited app=%s: %s", app_id, e)
        raise

    # 2. 归一化
    try:
        incoming = normalize_im_event(payload)
    except NormalizeError as e:
        logger.warning("normalize_failed: %s", e)
        raise

    # 3. 幂等去重
    idem_key = build_idempotency_key(app_id, incoming.chat_id, incoming.message_id)
    if app.state.session_factory is not None:
        factory = app.state.session_factory
    else:
        from persistence.engine import get_engine
        factory = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        idem_repo = IdempotencyRepo(s)
        if not idem_repo.try_reserve(idem_key):
            logger.info("duplicate event key=%s", idem_key)
            return {"status": "duplicate", "idempotency_key": idem_key}
        s.commit()  # 确保幂等行对后续请求可见
    finally:
        s.close()

    # 4. 业务处理（orchestrator 共享主 session：成功后 commit，异常 rollback。
    #    repo 层只 flush 不 commit，生产组装必须把主 session 挂到
    #    app.state.main_session，否则 sessions/tasks 等写入永不落库）
    main_session = getattr(app.state, "main_session", None)
    try:
        result = ctx.orchestrator.process(incoming)
    except Exception:
        if main_session is not None:
            main_session.rollback()
        raise
    if main_session is not None:
        main_session.commit()

    # 5. 关联 task_id 到幂等键
    task_id = result.get("task_id") if isinstance(result, dict) else None
    if task_id:
        s = factory()
        try:
            idem_repo = IdempotencyRepo(s)
            idem_repo.link_task(idem_key, task_id)
            s.commit()
        finally:
            s.close()

    return result


def _audit_event(app: FastAPI, *, actor_type: str, actor_id: str,
                 action: str, target_type: str, target_id: str,
                 detail: dict) -> None:
    """audit_logs 追加一条（session 缺省回退全局 engine；失败仅记日志）。"""
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from persistence.engine import get_engine
        factory = sessionmaker(bind=get_engine(), expire_on_commit=False,
                               autoflush=False)
    try:
        s = factory()
        try:
            from persistence.repositories.audit_repo import AuditRepo
            from shared.ulid_ import new_ulid
            AuditRepo(s).write(
                audit_id=new_ulid(),
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
            )
            s.commit()
        finally:
            s.close()
    except Exception:
        logger.exception("audit write failed (ignored)")


def process_card_payload(app: FastAPI, payload: dict) -> dict:
    """卡片回调公共处理（ADR-0031）：audit 落库 + renew_bind 分支。

    输入为平铺 dict（open_id/action/approval_id/session_id...，
    与 webhook 卡片路由验签后的 payload 结构一致）；ws 卡片适配层产出同构 dict。
    """
    _audit_event(
        app,
        actor_type="user",
        actor_id=payload.get("open_id", ""),
        action=f"card_{payload.get('action', 'unknown')}",
        target_type="approval",
        target_id=payload.get("approval_id", "")
        or payload.get("doc_write_id", "")
        or payload.get("skill_improve_id", "")
        or payload.get("code_approval_id", ""),
        detail=payload,
    )
    # renew_bind 分支（Phase 3）
    ctx: AppContext = app.state.ctx
    action = payload.get("action", "")
    if action == "renew_bind":
        session_id = payload.get("session_id", "")
        bind_doc_service = ctx.bind_doc_service
        if bind_doc_service is None:
            logger.warning("renew_bind received but bind_doc_service not configured")
            return {"ok": False, "reason": "bind_doc_service not configured"}
        try:
            new_exp = bind_doc_service.renew(session_id=session_id)
            main_session = getattr(app.state, "main_session", None)
            if main_session is not None:
                main_session.commit()
            return {"ok": True, "new_expires": new_exp.isoformat()}
        except Exception as e:
            logger.exception("renew_bind failed")
            return {"ok": False, "reason": str(e)}
    # research_writeback 分支（Phase 14）：写入决策到 broker，
    # 由 research 线程完成后续写入与回执（避免双线程写文档）
    # Phase 17：node_l2_approval（write_doc 节点级审批）同一处理逻辑——
    # 同样落 doc_writes（owner 校验复用）+ broker 决策
    if action in ("research_writeback", "node_l2_approval"):
        broker = ctx.approval_broker
        if broker is None:
            logger.warning("research_writeback received but broker not configured")
            return {"ok": False, "reason": "approval broker not configured"}
        # Phase 15 T1：仅任务发起者可决策（群聊其他成员点击无效）
        operator = payload.get("open_id", "")
        row = _doc_write_row(app, payload.get("doc_write_id", ""))
        if row is not None:
            owner, row_status = row
            if owner is not None and owner != operator:
                logger.warning(
                    "research_writeback forbidden: doc_write_id=%s operator=%s owner=%s",
                    payload.get("doc_write_id", ""), operator, owner)
                return {"ok": False, "status": "forbidden"}
            # 持久化幂等兜底：broker wait 消费后内存条目已清，
            # 行状态非 pending（approved/success/failed/cancelled）说明
            # 该卡片已处理过（真机 2026-09-01：二次点击曾再返回 decided）
            if row_status and row_status != "pending":
                logger.info(
                    "research_writeback dup on finished row: doc_write_id=%s status=%s",
                    payload.get("doc_write_id", ""), row_status)
                return {"ok": False, "status": "already_handled",
                        "decision": ""}
        decision = payload.get("decision", "")
        decided = broker.decide(
            payload.get("doc_write_id", ""), decision, operator=operator,
        )
        if not decided:
            logger.info("research_writeback ignored (dup/unknown/invalid): %s",
                        payload.get("doc_write_id", ""))
        return {"ok": decided,
                "status": "decided" if decided else "already_handled",
                "decision": decision if decided else ""}
    # Phase 26：code_approval 分支（/code 命令与 skill L2 工具审批）。
    # 审批项为会话级短生命周期、不落库：owner 由发卡时内嵌 value 比对
    if action == "code_approval":
        broker = ctx.approval_broker
        if broker is None:
            logger.warning("code_approval received but broker not configured")
            return {"ok": False, "reason": "approval broker not configured"}
        operator = payload.get("open_id", "")
        owner = payload.get("owner", "")
        if owner and operator and owner != operator:
            logger.warning("code_approval forbidden: operator=%s owner=%s",
                           operator, owner)
            return {"ok": False, "status": "forbidden"}
        decision = payload.get("decision", "")
        decided = broker.decide(
            payload.get("code_approval_id", ""), decision, operator=operator)
        return {"ok": decided,
                "status": "decided" if decided else "already_handled",
                "decision": decision if decided else ""}
    # Phase 27：skill_improve 分支（/code 失败后的 skill 改进审批）。
    # owner 内嵌 value 比对 + broker 幂等；批准后立即执行 diagnoser.apply 写回
    # skill 文件（无人 wait 该 id，broker 仅作首击固化/重复点击幂等）。
    # 卡片无 update 能力，处理结果经响应 JSON 返回（飞书以 toast 展示）。
    if action == "skill_improve":
        broker = ctx.approval_broker
        if broker is None:
            logger.warning("skill_improve received but broker not configured")
            return {"ok": False, "reason": "approval broker not configured"}
        operator = payload.get("open_id", "")
        owner = payload.get("owner", "")
        if owner and operator and owner != operator:
            logger.warning("skill_improve forbidden: operator=%s owner=%s",
                           operator, owner)
            return {"ok": False, "status": "forbidden"}
        decision = payload.get("decision", "")
        decided = broker.decide(
            payload.get("skill_improve_id", ""), decision, operator=operator)
        if not decided:
            return {"ok": False, "status": "already_handled", "decision": ""}
        if decision != "approve":
            return {"ok": True, "status": "decided", "decision": decision}
        diagnoser = getattr(
            getattr(ctx.orchestrator, "coding_runner", None), "diagnoser", None)
        if diagnoser is None:
            logger.warning("skill_improve approved but diagnoser not configured")
            return {"ok": False, "status": "decided", "decision": decision,
                    "reason": "skill diagnoser not configured"}
        try:
            suggestion = json.loads(payload.get("suggestion", "") or "{}")
        except ValueError:
            _audit_event(
                app, actor_type="system", actor_id="skill_diagnoser",
                action="skill_improve_apply_failed", target_type="skill",
                target_id=payload.get("skill_improve_id", ""),
                detail={"skill": "", "reason": "bad suggestion json"})
            return {"ok": False, "status": "apply_failed",
                    "reason": "bad suggestion json"}
        try:
            applied = diagnoser.apply(suggestion)
        except Exception as e:  # noqa: BLE001 —— 写回异常转为卡片可见错误
            logger.exception("skill_improve apply crashed")
            _audit_event(
                app, actor_type="system", actor_id="skill_diagnoser",
                action="skill_improve_apply_failed", target_type="skill",
                target_id=payload.get("skill_improve_id", ""),
                detail={"skill": suggestion.get("skill", ""),
                        "reason": str(e)})
            return {"ok": False, "status": "apply_failed", "reason": str(e)}
        if not applied.get("ok"):
            _audit_event(
                app, actor_type="system", actor_id="skill_diagnoser",
                action="skill_improve_apply_failed", target_type="skill",
                target_id=payload.get("skill_improve_id", ""),
                detail={"skill": suggestion.get("skill", ""),
                        "reason": applied.get("error", "")})
            return {"ok": False, "status": "apply_failed",
                    "reason": applied.get("error", "")}
        _audit_event(
            app, actor_type="system", actor_id="skill_diagnoser",
            action="skill_improve_applied", target_type="skill",
            target_id=payload.get("skill_improve_id", ""),
            detail={"skill": suggestion.get("skill", ""),
                    "file": applied.get("file", ""),
                    "backup": applied.get("backup", "")})
        return {"ok": True, "status": "applied",
                "file": applied.get("file", ""),
                "backup": applied.get("backup", "")}
    # Phase 38：research_intent 分支（意图预判确认卡，research/code 双路由）。
    # owner 内嵌比对 + 内存幂等（同 code_approval 模式）；批准后按路由转
    # ResearchRunner 或 CodingRunner.handle（受理即回 + 后台线程），等同
    # 用户发了 /research 或 /code。单挂载点：gate 只挂 orch，此处经
    # ctx.orchestrator 取同实例。
    if action == "research_intent":
        orch = ctx.orchestrator
        gate = getattr(orch, "intent_gate", None)
        if gate is None:
            return {"ok": False, "status": "intent_unavailable"}
        result = gate.decide(
            payload.get("intent_id", ""), payload.get("decision", ""),
            operator=payload.get("open_id", ""),
            owner=payload.get("owner", ""),
            route=payload.get("route", ""))
        if result.get("status") != "intent_approved":
            return result
        route = result.get("route", "research")
        runner = getattr(orch, "research_runner" if route == "research"
                         else "coding_runner", None)
        if runner is None:
            return {"ok": False, "status": "intent_unavailable"}
        from shared.schemas import IncomingMessage
        incoming = IncomingMessage(**result["incoming_kwargs"])
        runner.handle(incoming)
        _audit_event(
            app, actor_type="user", actor_id=payload.get("open_id", ""),
            action="intent_approved", target_type="research_intent",
            target_id=payload.get("intent_id", ""),
            detail={"route": route, "text": incoming.text[:200]})
        return {"ok": True, "status": "intent_approved", "route": route,
                "card": result.get("card")}
    # Phase 30：model_switch 分支（/model 状态卡按钮热切换主备模型）。
    # admin 校验在 service 内（复用 FEISHU_ADMIN_OPEN_IDS）；成败均落专项审计，
    # 结果经 toast 反馈（卡片无 update 能力，同 skill_improve 模式）。
    # 双卡片流程（ut-7）：model_pick/model_back 经回调响应卡原地换卡面。
    if action == "model_pick":
        svc = ctx.model_switch_service
        if svc is None:
            return {"ok": False, "status": "model_switch_unavailable"}
        card = svc.picker_card(payload.get("open_id", ""),
                               payload.get("slot", ""))
        if card is None:
            return {"ok": False, "status": "model_switch_denied",
                    "reason": "forbidden"}
        return {"ok": True, "status": "model_pick", "card": card}
    if action == "model_back":
        svc = ctx.model_switch_service
        if svc is None:
            return {"ok": False, "status": "model_switch_unavailable"}
        card = svc.status_card(payload.get("open_id", ""))
        if card is None:
            return {"ok": False, "status": "model_switch_denied",
                    "reason": "forbidden"}
        return {"ok": True, "status": "model_back", "card": card}
    if action == "model_switch":
        svc = ctx.model_switch_service
        if svc is None:
            logger.warning("model_switch received but service not configured")
            return {"ok": False, "status": "model_switch_unavailable"}
        operator = payload.get("open_id", "")
        name = payload.get("name", "")
        slot = payload.get("slot", "")
        result = svc.switch(operator, name, slot)
        if result.get("ok"):
            _audit_event(
                app, actor_type="user", actor_id=operator,
                action="llm_model_switched", target_type="llm_config",
                target_id=f"{slot}:{name}",
                detail={"slot": slot, "from": result.get("from", ""),
                        "to": result.get("to", "")})
            # 切换成功：附最新状态卡，回调响应把选模型卡原地刷回状态卡
            return {"ok": True, "status": "model_switched",
                    "slot": slot, "to": name,
                    "card": svc.status_card(operator)}
        _audit_event(
            app, actor_type="user", actor_id=operator,
            action="llm_model_switch_denied", target_type="llm_config",
            target_id=f"{slot}:{name}",
            detail={"reason": result.get("reason", "")})
        return {"ok": False, "status": "model_switch_denied",
                "reason": result.get("reason", "")}
    return {"ok": True}


def _doc_write_row(app: FastAPI, doc_write_id: str) -> tuple[str | None, str] | None:
    """查 doc_writes 行（发起者 open_id, status）；row 缺失返回 None（不拦截）。"""
    if not doc_write_id:
        return None
    if app.state.session_factory is not None:
        factory = app.state.session_factory
    else:
        from persistence.engine import get_engine
        factory = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)
    try:
        s = factory()
        try:
            from persistence.repositories.doc_write_repo import DocWriteRepo
            row = DocWriteRepo(s).get(doc_write_id)
            if row is None:
                return None
            return getattr(row, "requested_by", None), getattr(row, "status", "")
        finally:
            s.close()
    except Exception:
        logger.exception("doc_write row lookup failed (allow pass)")
        return None


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
    approval_broker: object | None = None  # Phase 14：写回审批决策传递
    comment_event_service: object | None = None  # Phase 18：webhook 评论事件
    tag_recommend_service: object | None = None  # Phase 19：标签推荐
    model_switch_service: object | None = None  # Phase 30：模型热切换


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
    approval_broker=None,
    comment_event_service=None,
    tag_recommend_service=None,
    model_switch_service=None,
) -> FastAPI:
    """工厂函数：创建并配置 FastAPI app。

    参数：
    - secret: 飞书 webhook 验签密钥
    - orchestrator: Orchestrator 实例（带 .process() 方法）
    - rate_per_min: 每分钟限流阈值
    - session_factory: 可调用对象，返回 Session；用于幂等表读写。
      不传则每次请求内即时从 persistence.engine.get_engine() 创建。
    """
    # Phase 22：on_event 弃用 → lifespan（行为等价迁移；Phase 9 auto_sync
    # 后台轮询逻辑不变，ADR-0024）
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        if auto_sync_worker is not None:
            auto_sync_worker.start_async()
        yield

    app = FastAPI(title="Feishu Research Agent — Phase 1",
                  lifespan=_lifespan)

    def _factory():
        if session_factory is not None:
            return session_factory()
        from persistence.engine import get_engine
        return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)()

    app.state.session_factory = session_factory
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
        approval_broker=approval_broker,
        comment_event_service=comment_event_service,
        tag_recommend_service=tag_recommend_service,
        model_switch_service=model_switch_service,
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

        流程：HMAC 验签 → 解析 body → 公共卡片处理管线（ADR-0031）。
        """
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
        # 验签通过：复用公共卡片处理管线（ADR-0031，ws 长连接同源）
        return process_card_payload(app, payload)

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

    @app.get("/templates/{template_id}/tag-suggestions")
    async def tag_suggestions(template_id: str, limit: int = 5):
        """Phase 19：标签推荐（共现 + 热度兜底，任意人可读）。"""
        ctx = app.state.ctx
        rs = ctx.tag_recommend_service
        if rs is None:
            raise _HTTPException(
                status_code=503,
                detail="tag_recommend_service not configured")
        try:
            suggestions = rs.suggest(template_id=template_id, limit=limit)
        except ValueError as e:
            raise _HTTPException(status_code=404, detail=str(e))
        return {"template_id": template_id, "suggestions": suggestions}

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

        body = json.loads(body_str)

        # 2. Phase 18：url_verification challenge 应答（事件订阅首次配置校验）
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}

        # 3. Phase 18：评论事件分流（ws 容灾通道；结构与 ws 事件同源，
        #    header.event_type 区分，event.notice_meta 提取上下文）
        header = body.get("header") or {}
        if header.get("event_type") == "drive.notice.comment_add_v1":
            svc = ctx.comment_event_service
            if svc is None:
                raise HTTPException(
                    status_code=503,
                    detail="comment_event_service not configured")
            event = body.get("event") or {}
            meta = event.get("notice_meta") or {}
            operator = meta.get("from_user_id") or {}
            result = svc.handle(
                file_token=meta.get("file_token", ""),
                operator_open_id=operator.get("open_id", ""),
                comment_id=event.get("comment_id", ""))
            logger.info("webhook comment event handled: %s", result)
            return result

        # 4. 限流/归一化/幂等/业务处理：公共管线（ADR-0031，ws 长连接同源）
        app_id = request.headers.get("X-Lark-App-Id", "default")
        try:
            return run_im_pipeline(app, app_id, body)
        except RateLimitExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))
        except NormalizeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FeishuAgentError as e:
            logger.exception("orchestrator process failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return app
