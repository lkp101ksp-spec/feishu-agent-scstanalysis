"""生产组装入口（Phase 10 联调补充轮，ADR-0031）。

从 settings + 环境变量装配真实对象图：LarkCLI adapters → LLMRouter →
repos/services 全家桶 → Orchestrator（Phase 5-9 服务双通道注入）→ create_app。

生命周期约定（联调低并发）：
- 全部 repo/service 绑定同一个长持有 Session（与既有 gateway 单例服务模式一致）；
- 幂等表读写仍走每请求独立 session（run_im_pipeline 内自建）。
多实例部署需另行改造（见 ADR-0024 分布式锁议题）。

可选环境变量（不设则对应子系统为 None，相关 API 返回 not configured）：
- FEISHU_BASE_APP_TOKEN                  : Base 投影（L0 read）
- FEISHU_DRIVE_PARENT_TOKEN              : Drive 上传父目录
- FEISHU_ADMIN_OPEN_IDS                  : 公共模板审核管理员，逗号分隔
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from config.settings import Settings, load_settings
from feishu_adapter.base_projection_adapter import BaseProjectionAdapter
from feishu_adapter.bot_info import get_bot_open_id
from feishu_adapter.client import LarkCLI
from feishu_adapter.comment_client import CommentClient
from feishu_adapter.doc_adapter import DocAdapter
from feishu_adapter.drive_adapter import DriveAdapter
from feishu_adapter.im_adapter import IMAdapter
from gateway.app import create_app
from orchestrator.app import Orchestrator
from orchestrator.approval_broker import ApprovalBroker
from orchestrator.approval_service import ApprovalService
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.llm_router import LLMRouter
from orchestrator.research_runner import ResearchRunner
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from orchestrator.templates.auto_sync_worker import CommentAutoSyncWorker
from orchestrator.templates.comment_action_service import CommentActionService
from orchestrator.templates.comment_event_service import CommentEventService
from orchestrator.templates.comment_service import CommentService
from orchestrator.templates.comment_sync_service import CommentSyncService
from orchestrator.templates.diff_service import VersionDiffService
from orchestrator.templates.favorite_service import FavoriteService
from orchestrator.templates.fork_service import ForkService
from orchestrator.templates.notify_service import CommentNotifyService
from orchestrator.templates.public_service import PublicTemplateService
from orchestrator.templates.search_service import TemplateSearchService
from orchestrator.templates.share_service import ShareService
from orchestrator.templates.tag_recommend_service import TagRecommendService
from orchestrator.templates.tag_service import TagService
from orchestrator.templates.template_service import TemplateService
from orchestrator.templates.unified_search_service import UnifiedSearchService
from orchestrator.templates.version_service import VersionService
from orchestrator.tools.bio.rate_limiter import RateLimiter
from persistence.engine import get_engine
from persistence.repositories.artifact_repo import ArtifactRepo
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.comment_notify_repo import CommentNotifyRepo
from persistence.repositories.comment_repo import CommentRepo
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from persistence.repositories.task_repo import TaskRepo
from persistence.repositories.template_favorite_repo import TemplateFavoriteRepo
from persistence.repositories.template_repo import TemplateRepo
from persistence.repositories.template_tag_repo import TemplateTagRepo
from persistence.repositories.template_version_repo import TemplateVersionRepo

logger = logging.getLogger(__name__)


class Runtime:
    """组装产物容器：app（FastAPI）+ ws 进程所需句柄。"""

    def __init__(self, app, orchestrator: Orchestrator, settings: Settings,
                 renew_scan_service=None, renew_scan_interval_sec: int = 60,
                 comment_event_service=None, auto_sync_worker=None):
        self.app = app
        self.orchestrator = orchestrator
        self.settings = settings
        # 续期卡片扫描（独立 session，避免与主管线共享 Session 跨线程竞争）
        self.renew_scan_service = renew_scan_service
        self.renew_scan_interval_sec = renew_scan_interval_sec
        # 评论事件处理（独立 event_session：ws 回调线程隔离，ADR-0033）
        self.comment_event_service = comment_event_service
        # 评论轮询兜底（独立 scan_session：ws_client 守护线程隔离）
        self.auto_sync_worker = auto_sync_worker


def build_runtime(settings: Settings | None = None) -> Runtime:
    """装配全部真实组件并构造 FastAPI app（ws_client 与 uvicorn 共用）。"""
    if settings is None:
        settings = load_settings()

    session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()

    # --- 出站适配层（联调轮：IM/Doc 走 lark-oapi SDK tenant 直连；Base/Drive 仍走 lark-cli） ---
    import lark_oapi as lark

    sdk = (lark.Client.builder()
           .app_id(settings.feishu.app_id)
           .app_secret(settings.feishu.app_secret)
           .log_level(lark.LogLevel.INFO)
           .build())
    cli = LarkCLI()
    im = IMAdapter(cli=cli, sdk_client=sdk)
    doc = DocAdapter(cli=cli, sdk_client=sdk)
    base = None
    if os.environ.get("FEISHU_BASE_APP_TOKEN"):
        base = BaseProjectionAdapter(os.environ["FEISHU_BASE_APP_TOKEN"], cli=cli)
    drive = None
    artifact_repo = ArtifactRepo(session)
    if os.environ.get("FEISHU_DRIVE_PARENT_TOKEN"):
        drive = DriveAdapter(
            parent_node_token=os.environ["FEISHU_DRIVE_PARENT_TOKEN"],
            lark_cli=cli, artifact_repo=artifact_repo,
            max_size_mb=settings.drive_max_file_size_mb,
        )

    # --- Phase 1 主链路 ---
    llm = LLMRouter(
        primary={
            "base_url": settings.llm.primary_base_url,
            "api_key": settings.llm.primary_api_key,
            "model": settings.llm.primary_model,
            "timeout_sec": settings.llm_timeout_sec,
        },
        fallback={
            "base_url": settings.llm.fallback_base_url,
            "api_key": settings.llm.fallback_api_key,
            "model": settings.llm.fallback_model,
            "timeout_sec": settings.llm_timeout_sec,
        },
        max_retries=settings.llm.max_retries,
    )
    audit_repo = AuditRepo(session)
    session_service = SessionService(SessionRepo(session), audit_repo=audit_repo)
    task_service = TaskService(TaskRepo(session), audit_repo)
    bind_doc_service = BindDocService(
        session_service, audit_repo, settings.bind_doc_ttl_sec,
        session_repo=SessionRepo(session), im_adapter=im,
        renew_threshold_sec=settings.bind_doc_renew_threshold_sec,
        doc_adapter=doc,
    )
    doc_write_service = DocWriteService(
        SessionRepo(session), DocWriteRepo(session), doc,
    )

    orch = Orchestrator(
        llm, session_service, task_service, bind_doc_service, doc_write_service, im,
        settings=settings, doc_adapter=doc, base_adapter=base, drive_adapter=drive,
        audit_repo=audit_repo, artifact_repo=artifact_repo,
    )

    # --- Phase 5-6：模板库 ---
    template_repo = TemplateRepo(session)
    version_service = VersionService(TemplateVersionRepo(session), template_repo)
    template_service = TemplateService(template_repo)
    share_service = ShareService(template_repo)
    admin_ids = {
        x.strip()
        for x in os.environ.get("FEISHU_ADMIN_OPEN_IDS", "").split(",")
        if x.strip()
    }
    public_service = PublicTemplateService(
        template_repo=template_repo, audit_repo=audit_repo,
        admin_user_ids=admin_ids,
    )
    fork_service = ForkService(template_repo)
    tag_service = TagService(TemplateTagRepo(session), template_repo)
    # Phase 19：标签推荐（共现 + 热度兜底，spec 2026-09-01 phase19）
    tag_recommend_service = TagRecommendService(
        TemplateTagRepo(session), template_repo)
    favorite_service = FavoriteService(TemplateFavoriteRepo(session), template_repo)
    diff_service = VersionDiffService(
        TemplateVersionRepo(session), template_repo,
    )
    unified_search_service = UnifiedSearchService(
        template_repo, TemplateTagRepo(session), TemplateFavoriteRepo(session),
    )
    orch.version_service = version_service
    orch.template_service = template_service
    orch.share_service = share_service
    orch.public_service = public_service
    orch.fork_service = fork_service
    orch.tag_service = tag_service
    orch.favorite_service = favorite_service
    orch.diff_service = diff_service
    orch.unified_search_service = unified_search_service

    # --- Phase 7-9：评论闭环（SDK 直连零凭据，ADR-0032；无条件组装） ---
    comment_client = CommentClient(
        sdk_client=sdk, rate_limiter=RateLimiter(rate=3.0, per_sec=1.0),
    )
    comment_service = CommentService(comment_client)
    comment_repo = CommentRepo(session)
    comment_sync_service = CommentSyncService(comment_client, comment_repo)
    comment_action_service = CommentActionService(
        comment_repo, template_repo, version_service,
        comment_client=comment_client,
    )
    notify = CommentNotifyService(
        comment_repo, CommentNotifyRepo(session), im,
        notify_all=settings.comment_notify_all,
    )
    auto_sync_worker_startup = CommentAutoSyncWorker(
        session_repo=SessionRepo(session),
        sync_service=comment_sync_service,
        notify_service=notify,
        interval_sec=settings.comment_sync_interval_sec,
    )
    orch.comment_service = comment_service
    orch.comment_sync_service = comment_sync_service
    orch.comment_action_service = comment_action_service

    # --- 卡片回调 HMAC（Phase 2） ---
    _approval = ApprovalService(secret=settings.approval_hmac_secret)  # noqa: F841 预热校验

    # --- ApprovalBroker（Phase 14：写回审批决策跨线程传递） ---
    # research 线程 wait / ws 卡片回调线程 decide 共用同一实例；
    # 启动清扫遗留 card_confirm pending（重启后无人等待的孤儿记录）
    approval_broker = ApprovalBroker()
    orch.approval_broker = approval_broker
    try:
        stale = doc_write_service.cancel_stale_pending()
        if stale:
            logger.info("cancelled %d stale card_confirm pending(s)", stale)
    except Exception:
        logger.exception("cancel stale pending failed (ignored)")

    # --- 评论事件服务（独立 event_session：ws/webhook 回调线程隔离，ADR-0033） ---
    # Phase 18：/ask 问答依赖注入（doc_adapter/llm/qa_reply_client，
    # 任一缺失自动禁用问答不回归）；create_app 前构造以便 webhook 分流注入
    event_session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()
    event_comment_client = CommentClient(
        sdk_client=sdk, rate_limiter=RateLimiter(rate=3.0, per_sec=1.0))
    comment_event_service = CommentEventService(
        session_repo=SessionRepo(event_session),
        sync_service=CommentSyncService(
            event_comment_client, CommentRepo(event_session)),
        notify_service=CommentNotifyService(
            CommentRepo(event_session), CommentNotifyRepo(event_session), im,
            notify_all=settings.comment_notify_all),
        bot_open_id=get_bot_open_id(sdk),
        session=event_session,
        comment_repo=CommentRepo(event_session),
        doc_adapter=getattr(orch, "doc_adapter", None),
        llm=getattr(orch, "llm", None),
        qa_reply_client=event_comment_client,
    )

    app = create_app(
        secret=settings.feishu.webhook_secret,
        orchestrator=orch,
        rate_per_min=settings.rate_limit_per_min,
        session_factory=lambda: sessionmaker(
            bind=get_engine(), expire_on_commit=False, autoflush=False,
        )(),
        bind_doc_service=bind_doc_service,
        template_service=template_service,
        version_service=version_service,
        share_service=share_service,
        comment_service=comment_service,
        search_service=TemplateSearchService(template_repo),
        public_service=public_service,
        fork_service=fork_service,
        comment_sync_service=comment_sync_service,
        comment_action_service=comment_action_service,
        diff_service=diff_service,
        tag_service=tag_service,
        favorite_service=favorite_service,
        unified_search_service=unified_search_service,
        auto_sync_worker=auto_sync_worker_startup,
        approval_broker=approval_broker,
        comment_event_service=comment_event_service,
        tag_recommend_service=tag_recommend_service,
    )
    # 主 session 挂载：run_im_pipeline / 卡片管线在处理成功后负责 commit
    app.state.main_session = session

    # --- 续期卡片扫描服务（独立 session：扫描线程与主管线不共享 Session） ---
    scan_session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()
    renew_scan_service = BindDocService(
        SessionService(SessionRepo(scan_session),
                       audit_repo=AuditRepo(scan_session)),
        AuditRepo(scan_session), settings.bind_doc_ttl_sec,
        session_repo=SessionRepo(scan_session), im_adapter=im,
        renew_threshold_sec=settings.bind_doc_renew_threshold_sec,
    )

    # --- 轮询兜底 worker（独立 session：ws_client 守护线程隔离，ADR-0033） ---
    poll_session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()
    auto_sync_worker = CommentAutoSyncWorker(
        session_repo=SessionRepo(poll_session),
        sync_service=CommentSyncService(
            CommentClient(sdk_client=sdk,
                          rate_limiter=RateLimiter(rate=3.0, per_sec=1.0)),
            CommentRepo(poll_session)),
        notify_service=CommentNotifyService(
            CommentRepo(poll_session), CommentNotifyRepo(poll_session), im,
            notify_all=settings.comment_notify_all),
        interval_sec=settings.comment_sync_interval_sec,
        session=poll_session,
    )
    # --- ResearchRunner（Phase 12：/research 后台线程，独立 session 惰性新建） ---
    research_session_factory = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )
    orch.research_runner = ResearchRunner(
        orchestrator=orch, session_factory=research_session_factory,
    )
    # Phase 26：/code agentic coding 链路（CodingRunner 三层工具面拼装；
    # tool_handler/registry 复用 Orchestrator.__init__ 已装配的实例）
    from orchestrator.coding.coding_runner import CodingRunner
    orch.coding_runner = CodingRunner(
        llm=llm, im=im, tool_handler=orch.tool_handler,
        registry=orch.registry, broker=approval_broker, settings=settings,
    )
    return Runtime(
        app=app, orchestrator=orch, settings=settings,
        renew_scan_service=renew_scan_service,
        renew_scan_interval_sec=settings.bind_doc_renew_card_interval_sec,
        comment_event_service=comment_event_service,
        auto_sync_worker=auto_sync_worker,
    )


def build_app() -> "FastAPI":
    """uvicorn --factory 入口：组装真实组件并返回 FastAPI app。"""
    return build_runtime().app
