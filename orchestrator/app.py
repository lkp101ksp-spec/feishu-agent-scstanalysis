"""Orchestrator 主流程：串联 Session / BindDoc / Task / LLM / IM / Doc-Write。

业务流程：
1. 收到 IncomingMessage
2. 若为 /bind-doc 指令：调用 BindDocService.bind + 回复 IM
3. 否则：创建 task → LLM 生成 reply → IM 回复 → 若有有效 bind 则写文档 → 收尾 task 状态

Phase 2 增量：保留 process() Phase 1 路径；新增 process_phase2() 走 Planner + Scheduler。
返回 dict 结构（status / task_id / session_id / reply_text / doc_written / doc_id / warning）。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.bind_doc_service import BindDocService
from orchestrator.doc_write_service import DocWriteService
from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.local_executor import LocalExecutor
from orchestrator.executor.sandbox import DockerSandbox, DockerSandboxConfig
from orchestrator.llm_router import LLMRouter
from orchestrator.planner.planner import Planner
from orchestrator.planner.scheduler import Scheduler
from orchestrator.session_service import SessionService
from orchestrator.task_service import TaskService
from orchestrator.template_engine import TemplateEngine
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry, parse_disabled_tools
from shared.errors import BindDocInvalidError, DocWriteError, FeishuAgentError, LLMCallError
from shared.schemas import ChatMessage, IncomingMessage

if TYPE_CHECKING:
    # gateway/runtime.py 组装期挂载到 Orchestrator 的服务（仅类型注解用，
    # 运行时不导入，避免循环依赖与启动开销）
    from orchestrator.approval_broker import ApprovalBroker
    from orchestrator.chat_memory import ChatMemory
    from orchestrator.coding.coding_runner import CodingRunner
    from orchestrator.intent_gate import IntentGateService
    from orchestrator.model_switch_service import ModelSwitchService
    from orchestrator.research_runner import ResearchRunner
    from orchestrator.templates.comment_action_service import CommentActionService
    from orchestrator.templates.comment_service import CommentService
    from orchestrator.templates.comment_sync_service import CommentSyncService
    from orchestrator.templates.diff_service import VersionDiffService
    from orchestrator.templates.favorite_service import FavoriteService
    from orchestrator.templates.fork_service import ForkService
    from orchestrator.templates.public_service import PublicTemplateService
    from orchestrator.templates.share_service import ShareService
    from orchestrator.templates.tag_service import TagService
    from orchestrator.templates.template_service import TemplateService
    from orchestrator.templates.unified_search_service import UnifiedSearchService
    from orchestrator.templates.version_service import VersionService

SYSTEM_PROMPT = "你是飞书科研助手。请用简洁中文回答，不超过 200 字。"

logger = logging.getLogger(__name__)


class Orchestrator:
    """Phase 1 主流程编排器。"""

    # --- gateway/runtime.py 组装期挂载的 Phase 5+ 服务（构造后赋值；
    # 此处仅作类级类型声明，不生成任何运行时代码） ---
    version_service: VersionService
    template_service: TemplateService
    share_service: ShareService
    public_service: PublicTemplateService
    fork_service: ForkService
    tag_service: TagService
    favorite_service: FavoriteService
    diff_service: VersionDiffService
    unified_search_service: UnifiedSearchService
    comment_service: CommentService
    comment_sync_service: CommentSyncService
    comment_action_service: CommentActionService
    approval_broker: ApprovalBroker
    research_runner: ResearchRunner
    intent_gate: IntentGateService
    coding_runner: CodingRunner
    model_switch_service: ModelSwitchService
    # 长会话记忆（2026-09-08 spec）：runtime.py 装配；未装配时闲聊为无记忆单轮
    chat_memory: ChatMemory

    def __init__(
        self,
        llm_router: LLMRouter,
        session_service: SessionService,
        task_service: TaskService,
        bind_doc_service: BindDocService,
        doc_write_service: DocWriteService,
        im_adapter: IMAdapter,
        *,
        settings: Any = None,
        doc_adapter: Any = None,
        base_adapter: Any = None,
        drive_adapter: Any = None,
        audit_repo: Any = None,
        artifact_repo: Any = None,
        research_llm: Optional[LLMRouter] = None,
    ):
        self.llm = llm_router
        # Phase 44：/research 场景 router（planner + scheduler 条件/修复判定）；
        # 缺省跟随全局（/model 热切换语义不变）
        self.research_llm = research_llm or llm_router
        self.session_service = session_service
        self.task_service = task_service
        self.bind_doc_service = bind_doc_service
        self.doc_write_service = doc_write_service
        self.im = im_adapter

        # Phase 2 子系统（可选注入；settings+doc_adapter 具备即初始化，
        # base/drive 缺失时对应工具跳过注册——Phase 12 板块②）
        self.settings = settings
        self.doc_adapter = doc_adapter
        self.base_adapter = base_adapter
        self.drive_adapter = drive_adapter
        self.audit_repo = audit_repo
        self.artifact_repo = artifact_repo

        if settings is not None and doc_adapter is not None:
            self.registry = ToolRegistry()
            # 沙箱先于工具注册创建（T2：run_python 注册时就要拿到 kernel_pool）
            cfg = DockerSandboxConfig.from_settings(settings)
            self.sandbox = DockerSandbox(cfg)
            self.kernel_pool = KernelPool(
                sandbox=self.sandbox,
                idle_timeout_sec=settings.kernel_idle_timeout_sec,
            )
            from orchestrator.tools.builtin.l0_read import register_l0_read
            from orchestrator.tools.builtin.l1_compute import register_l1_compute
            from orchestrator.tools.builtin.l2_side_effect import register_l2_side_effect
            register_l0_read(
                self.registry,
                doc_adapter=doc_adapter,
                base_adapter=base_adapter,
                drive_adapter=drive_adapter,
            )
            register_l1_compute(
                self.registry, llm_router=llm_router,
                kernel_manager=self.kernel_pool,  # T2：真沙箱注入
                exec_timeout_sec=settings.kernel_exec_timeout_sec,
            )
            register_l2_side_effect(
                self.registry,
                doc_adapter=doc_adapter,
                base_adapter=base_adapter,
                im_adapter=im_adapter,
                drive_adapter=drive_adapter,
            )
            # === Phase 4 MVP: L3 领域工具 ===
            from orchestrator.tools.builtin.l3_bio import register_l3_bio
            register_l3_bio(self.registry)
            # === Phase 20: 单细胞 sc_* 工具（bio 容器） ===
            # 白名单为空则不注册（sc_load 无合法数据路径，注册无意义）
            from orchestrator.tools.bio.bio_runner import BioRunner
            from orchestrator.tools.builtin.l3_singlecell import (
                register_l3_singlecell,
            )
            bio_data_roots = [
                r.strip() for r in (settings.bio_data_roots or "").split(",")
                if r.strip()
            ]
            if bio_data_roots:
                Path(settings.bio_workspace_root).mkdir(
                    parents=True, exist_ok=True)
                bio_runner = BioRunner(
                    image=settings.bio_image,
                    workspace_root=settings.bio_workspace_root,
                    data_roots=bio_data_roots,
                    timeout_sec=settings.bio_script_timeout_sec,
                    cpus=settings.bio_cpus,
                    memory=settings.bio_memory,
                )
                register_l3_singlecell(
                    self.registry, bio_runner,
                    bio_use_gpu=settings.bio_use_gpu,
                    bio_gpu_image=settings.bio_gpu_image,
                    bio_scenic_db_root=settings.bio_scenic_db_root,
                )
                # === Phase 21: 空间转录组 st_* 工具（st 镜像 + /opt/st_tools） ===
                from orchestrator.tools.builtin.l3_spatial import (
                    register_l3_spatial,
                )
                register_l3_spatial(
                    self.registry, bio_runner,
                    st_image=settings.bio_st_image,
                    st_deconvolve_timeout=settings.st_deconvolve_timeout_sec)
                logger.info(
                    "Phase 21 st_* tools registered: image=%s",
                    settings.bio_st_image,
                )
                logger.info(
                    "Phase 20 sc_* tools registered: image=%s roots=%s "
                    "workspace=%s",
                    settings.bio_image, bio_data_roots,
                    settings.bio_workspace_root,
                )
            self.tool_handler = ToolHandler(
                registry=self.registry,
                settings=settings,  # Phase 16 ACL：执行层禁用名单兜底
            )
            self.executor = LocalExecutor(
                kernel_pool=self.kernel_pool, tool_handler=self.tool_handler
            )
            self.planner = Planner(llm_router=self.research_llm)
            self.template = TemplateEngine()

    def process(self, incoming: IncomingMessage) -> dict[str, Any]:
        """处理一条入站消息，返回结果摘要。"""
        # 1. /bind-doc 指令：单独分支
        if incoming.is_bind_doc_cmd and incoming.bind_doc_id:
            return self._handle_bind(incoming)

        # 1.2 /bind-doc-renew 指令：手动续期（卡片链路的命令行等价物）
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "session_id": session_id,
                        "error": str(e)}
            self.im.reply(incoming.chat_id,
                          f"[成功] 已续期到 {new_exp.isoformat()}")
            return {"status": "renew_bind", "session_id": session_id,
                    "new_expires": new_exp.isoformat()}

        # 1.25 /clear 指令：手动冻结当前会话开新会话（长会话记忆重置入口）
        if incoming.text.strip() == "/clear":
            memory = getattr(self, "chat_memory", None)
            if memory is None:
                self.im.reply(incoming.chat_id, "[提示] 长会话记忆未装配")
                return {"status": "clear_unavailable"}
            clear_sid = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            memory.clear(clear_sid)
            self.im.reply(incoming.chat_id, "[成功] 已开启新会话，历史已清空")
            return {"status": "cleared", "session_id": clear_sid}

        # 1.5 #写到 语法不完整（有锚点没正文）：提示用法，不进 LLM
        if incoming.write_anchor and not incoming.text.strip():
            self.im.reply(
                incoming.chat_id,
                "「#写到」用法：#写到 <章节标题> | <消息内容>，"
                "例如：#写到 1 测试 | 帮我记录今天的分析结论",
            )
            return {"status": "skipped", "reason": "write_to_usage"}

        # 1.6 模板市场/评论指令（Phase 5-9 全量 12 条）：命中直接返回，不进 LLM
        market_result = self._try_market_commands(incoming)
        if market_result is not None:
            return market_result

        # 1.65 /research 指令：研究任务后台执行（Phase 12 板块①⑤）
        stripped = incoming.text.strip()
        if stripped == "/research" or stripped.startswith("/research "):
            runner = getattr(self, "research_runner", None)
            if runner is None:
                self.im.reply(incoming.chat_id, "[错误] 研究引擎未配置")
                return {"status": "research_unavailable"}
            research_result: dict[str, Any] = runner.handle(incoming)
            return research_result

        # 1.68 /code 指令：agentic coding 后台执行（Phase 26）
        if stripped == "/code" or stripped.startswith("/code "):
            runner = getattr(self, "coding_runner", None)
            if runner is None:
                self.im.reply(incoming.chat_id, "[错误] coding 引擎未配置")
                return {"status": "coding_unavailable"}
            coding_result: dict[str, Any] = runner.handle(incoming)
            return coding_result

        # 1.69 /model 指令：模型切换状态卡（Phase 30，admin 限定）
        if stripped == "/model" or stripped.startswith("/model "):
            svc = getattr(self, "model_switch_service", None)
            if svc is None:
                self.im.reply(incoming.chat_id,
                              "[错误] 模型切换未配置（config/llm.yaml providers 段）")
                return {"status": "model_switch_unavailable"}
            card = svc.status_card(incoming.sender_open_id)
            if card is None:
                self.im.reply(
                    incoming.chat_id,
                    "[拒绝] /model 仅管理员可用（FEISHU_ADMIN_OPEN_IDS 名单内）",
                )
                return {"status": "model_switch_forbidden"}
            self.im.send_card(incoming.chat_id, card)
            return {"status": "model_switch_card_sent"}

        # 1.72 未知斜杠命令兜底：已知指令此前已全部路由（市场 12 条 +
        # /research、/code、/model），走到这里的 / 开头消息即未注册命令——
        # 给可用命令提示，而非静默落入闲聊 LLM（Phase 39 真机 /clear 困惑暴露）。
        if stripped.startswith("/"):
            self.im.reply(
                incoming.chat_id,
                f"[未知命令] {stripped.split()[0]}\n"
                "可用命令：/research <任务>（研究分析）· /code <任务>（代码任务）· "
                "/code clear（清空工作区）· /model（模型切换，管理员）· "
                "/bind-doc <doc_id>（绑定文档）· /clear（清空会话记忆）· /template-list（我的模板）\n"
                "或直接发自然语言，我会自动判断任务意图。",
            )
            return {"status": "unknown_command",
                    "command": stripped.split()[0]}

        # 1.7 群聊门控：群聊只响应指令（/ 开头、#写到），闲聊静默忽略防刷屏；
        # 私聊（p2p）行为不变。指令此前已全部路由，走到这里的群消息即闲聊。
        if getattr(incoming, "chat_type", "") == "group" and not (
                incoming.is_bind_doc_cmd
                or incoming.text.strip().startswith("/")
                or incoming.write_anchor):
            return {"status": "skipped", "reason": "group_non_command"}

        # 1.8 意图预判闸（Phase 38）：疑似研究意图发确认卡，用户点「确认执行」
        # 才转 /research；未命中/未装配/分类失败均落回普通闲聊路径。
        gate = getattr(self, "intent_gate", None)
        if gate is not None:
            offered: Optional[dict[str, Any]] = gate.maybe_offer(incoming)
            if offered is not None:
                return offered

        # 2. 普通消息：创建 session + task
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="general_chat",
        )

        # 3. LLM 生成回复（长会话记忆：装配了 chat_memory 时注入历史，
        #    压缩/冻结由 ChatMemory 编排；freeze 后 session_id 换为新会话，
        #    下游 bound_doc/写文档随新会话走——绑定已被 freeze_session 继承）
        memory = getattr(self, "chat_memory", None)
        if memory is None:
            messages = [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=incoming.text),
            ]
        else:
            history, session_id, _frozen = memory.prepare(
                session_id=session_id, chat_id=incoming.chat_id)
            messages = ([ChatMessage(role="system", content=SYSTEM_PROMPT)]
                        + history
                        + [ChatMessage(role="user", content=incoming.text)])
        try:
            reply_text = self.llm.chat(messages)
        except LLMCallError as e:
            self.task_service.mark_failed(
                task_id=task_id, error_code="LLM_FAILED", error_message=str(e)
            )
            self.im.reply(incoming.chat_id, f"[错误] LLM 调用失败：{e}")
            return {
                "status": "failed",
                "task_id": task_id,
                "session_id": session_id,
                "error": str(e),
            }

        # 4. IM 回复（写文档之前先回，保证用户先看到内容）
        self.im.reply(incoming.chat_id, reply_text)

        # 3.5 记忆落库（回复先行，写库在后：崩溃最多丢一轮记忆，可接受）
        if memory is not None:
            memory.append_turn(session_id, incoming.text, reply_text)

        # 5. 决定是否写文档
        bound_doc = self.session_service.bound_doc_id(session_id)
        doc_written = False
        doc_id: Optional[str] = None
        warning: Optional[str] = None
        if bound_doc:
            try:
                result = self.doc_write_service.write_plain_text(
                    session_id=session_id,
                    task_id=task_id,
                    requested_by=incoming.sender_open_id,
                    text=reply_text,
                    anchor_text=incoming.write_anchor,
                )
                doc_written = True
                doc_id = result["doc_id"]
            except DocWriteError as e:
                warning = str(e)

        # 6. 收尾 task 状态
        if warning:
            self.task_service.mark_partial_failure(
                task_id=task_id, reply_text=reply_text, warning=warning
            )
            status = "success_with_partial_failure"
        else:
            self.task_service.mark_success(task_id=task_id, reply_text=reply_text)
            status = "success"

        return {
            "status": status,
            "task_id": task_id,
            "session_id": session_id,
            "reply_text": reply_text,
            "doc_written": doc_written,
            "doc_id": doc_id,
            "warning": warning,
        }

    def _handle_bind(self, incoming: IncomingMessage) -> dict[str, Any]:
        """处理 /bind-doc <doc_id> 指令。"""
        # process() 入口已用 is_bind_doc_cmd and bind_doc_id 守卫，此处收窄类型
        assert incoming.bind_doc_id is not None
        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        try:
            expires_at = self.bind_doc_service.bind(
                session_id=session_id,
                owner_open_id=incoming.sender_open_id,
                doc_id=incoming.bind_doc_id,
                anchor=incoming.bind_anchor,
            )
        except BindDocInvalidError as e:
            self.im.reply(incoming.chat_id, f"[错误] bind-doc 失败：{e}")
            return {
                "status": "bind_doc_failed",
                "session_id": session_id,
                "error": str(e),
            }
        except FeishuAgentError as e:
            self.im.reply(incoming.chat_id, f"[错误] bind-doc 失败：{e}")
            return {
                "status": "bind_doc_failed",
                "session_id": session_id,
                "error": str(e),
            }

        anchor_tip = (
            f"，写入位置锚点：{incoming.bind_anchor!r}（插到该章节下方）"
            if incoming.bind_anchor else ""
        )
        self.im.reply(
            incoming.chat_id,
            f"[成功] 已绑定文档 {incoming.bind_doc_id}，授权有效期至 "
            f"{expires_at.isoformat()}{anchor_tip}。",
        )
        return {
            "status": "bind_doc_success",
            "session_id": session_id,
            "bound_doc_id": incoming.bind_doc_id,
            "expires_at": expires_at.isoformat(),
        }

    # === Phase 2 ===

    def process_phase2(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 2 主流程：Planner → Scheduler → Template → DocWrite。

        Phase 2 简化版：sync 包装 asyncio.run_until_complete；
        Phase 2.1 引入完整 asyncio event loop。
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 2 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )

        session_id = self.session_service.get_or_create(
            owner_open_id=incoming.sender_open_id,
            source_chat_id=incoming.chat_id,
        )
        task_id = self.task_service.create(
            session_id=session_id,
            message_id=incoming.message_id,
            intent="phase2_plan",
        )

        # 只暴露 Planner 可见工具（stub 隐藏，Phase 12 板块④）；
        # Phase 16 ACL：禁用名单内工具同样不给模型
        disabled = parse_disabled_tools(
            getattr(self.settings, "disabled_tools", ""))
        visible = [
            t for t in self.registry.list(planner_visible=True)
            if t.risk_level != "L2_side_effect" and t.name not in disabled
        ]
        available_tools = [t.name for t in visible]
        tools_schema = [t.to_openai_function() for t in visible]
        try:
            plan = self.planner.plan(
                message=incoming.text,
                session_id=session_id,
                task_id=task_id,
                available_tools=available_tools,
                tools_schema=tools_schema,
            )
        except Exception as e:
            self.task_service.mark_failed(
                task_id=task_id, error_code="PLAN_FAILED", error_message=str(e)
            )
            return {
                "status": "plan_failed",
                "task_id": task_id,
                "session_id": session_id,
                "error": str(e),
            }

        scheduler = Scheduler(
            plan=plan, executor=self.executor,
            max_concurrent=self.settings.max_concurrent_nodes,
            condition_llm=getattr(self, "llm", None),
            code_repair_llm=getattr(self, "llm", None),
            node_repair_max_retries=getattr(
                self.settings, "node_repair_max_retries", 1),
        )
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(scheduler.run_until_done())
        finally:
            loop.close()

        # render_plan_summary_blocks + render_blocks 是真实链路
        # （原 append_blocks 不存在，真机 2026-08-30 发现）
        blocks = self.template.render_plan_summary_blocks(
            status=result.status,
            node_states={k: v.value for k, v in result.node_states.items()},
            artifacts_count=0,
        )

        # Phase 17：原 self.approval.policy.can_skip_approval 判定随
        # ApprovalService 装配移除——bound_doc_id 已含有效期校验，
        # 有效绑定即授权直写（bind_scope 语义不变）
        bound = self.session_service.bound_doc_id(session_id)
        if bound:
            self.doc_adapter.render_blocks(bound, blocks)

        self.task_service.mark_success(
            task_id=task_id, reply_text=f"Plan {result.status}"
        )
        return {
            "status": result.status,
            "task_id": task_id,
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "node_states": {k: v.value for k, v in result.node_states.items()},
        }

    # === Phase 3 ===
    def process_phase3(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 3 主流程：runtime 接管 + bind_doc 续期指令 + 上下文压缩。

        与 process_phase2 区别：
        - 新增 /bind-doc-renew 指令分支
        - Scheduler 注入 PlanRuntime（动态追加 / 循环 / 冻结 hook）
        - 长会话记忆（2026-09-08 落地）：process() 闲聊路径经 ChatMemory
          注入历史，压缩/冻结由 ContextCompressor + freeze_session 执行
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 3 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )

        # 1. /bind-doc-renew 指令
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {
                    "status": "renew_bind",
                    "session_id": session_id,
                    "new_expires": new_exp.isoformat(),
                }
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}

        # 2. 普通消息：复用 process_phase2 主路径（Phase 3 简化版）
        # 完整 dynamic-append / while / for / freeze 集成在 Phase 3.1
        return self.process_phase2(incoming)

    # === Phase 4 MVP ===
    def process_phase4(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 4 MVP：与 process_phase3 区别仅 ToolRegistry 多注册 blast_search。

        普通消息委托给 process_phase3。
        """
        if not hasattr(self, "planner"):
            raise FeishuAgentError(
                "Phase 4 subsystems not initialized; "
                "construct Orchestrator with settings + adapters."
            )
        # /bind-doc-renew 指令继承 Phase 3
        if incoming.text.strip() == "/bind-doc-renew":
            session_id = self.session_service.get_or_create(
                owner_open_id=incoming.sender_open_id,
                source_chat_id=incoming.chat_id,
            )
            try:
                new_exp = self.bind_doc_service.renew(session_id=session_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已续期到 {new_exp.isoformat()}")
                return {
                    "status": "renew_bind", "session_id": session_id,
                    "new_expires": new_exp.isoformat(),
                }
            except Exception as e:
                self.im.reply(incoming.chat_id, f"[错误] 续期失败：{e}")
                return {"status": "renew_bind_failed", "error": str(e)}
        # 普通消息：复用 process_phase3 路径
        return self.process_phase3(incoming)

    # === Phase 5 ===
    def process_phase5(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 5 入口：市场指令路由（_try_market_commands）+ 复用 process_phase4。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 5 subsystems not initialized")

        result = self._try_market_commands(incoming)
        if result is not None:
            return result

        # 普通消息：复用 process_phase4
        return self.process_phase4(incoming)

    # === Phase 6 ===
    def process_phase6(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 6 入口：市场指令路由 + 复用 process_phase5。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 6 subsystems not initialized")

        result = self._try_market_commands(incoming)
        if result is not None:
            return result

        # 普通消息：复用 process_phase5
        return self.process_phase5(incoming)

    # === Phase 7 ===
    def process_phase7(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 7 入口：市场指令路由 + 复用 process_phase6。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 7 subsystems not initialized")

        result = self._try_market_commands(incoming)
        if result is not None:
            return result

        # 普通消息：复用 process_phase6
        return self.process_phase6(incoming)

    # === Phase 8 ===
    def process_phase8(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 8 入口：市场指令路由 + 复用 process_phase7。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 8 subsystems not initialized")

        result = self._try_market_commands(incoming)
        if result is not None:
            return result

        # 普通消息：复用 process_phase7
        return self.process_phase7(incoming)

    # === Phase 9 ===
    def process_phase9(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Phase 9 入口：市场指令路由 + 复用 process_phase8。"""
        from shared.errors import FeishuAgentError
        if not hasattr(self, "planner"):
            raise FeishuAgentError("Phase 9 subsystems not initialized")

        result = self._try_market_commands(incoming)
        if result is not None:
            return result

        # 普通消息：复用 process_phase8
        return self.process_phase8(incoming)

    def _try_market_commands(
            self, incoming: IncomingMessage) -> Optional[dict[str, Any]]:
        """模板市场/评论闭环指令路由（Phase 5-9 全量 12 条）。

        生产 process() 与 process_phase5-9 共用本路由器，保证指令行为单份维护。
        命中指令则执行并返回结果 dict；非本批指令返回 None（调用方继续正常流程）。
        各服务为可选注入（getattr None 检查）：未配置时回复提示而非抛错，
        评论三件套已 SDK 化零凭据恒组装（ADR-0032），此处保留 None 防御仅供测试注入。
        """
        text = incoming.text.strip()

        # --- 模板列表（Phase 5） ---
        if text == "/template-list":
            ts = getattr(self, "template_service", None)
            if ts is None:
                self.im.reply(incoming.chat_id, "[错误] template_service 未配置")
                return {"status": "template_list_failed"}
            templates = ts.list_by_owner(incoming.sender_open_id)
            ids = [t.template_id for t in templates]
            self.im.reply(incoming.chat_id, f"您的模板: {', '.join(ids) or '(无)'}")
            return {"status": "template_listed", "templates": ids}

        # --- 模板版本回滚 / 共享（Phase 6） ---
        if text.startswith("/template-rollback "):
            parts = text.split()
            if len(parts) < 3:
                self.im.reply(incoming.chat_id,
                              "[错误] 用法: /template-rollback <id> <version>")
                return {"status": "rollback_failed", "reason": "bad_args"}
            tid, ver = parts[1], int(parts[2])
            vs = getattr(self, "version_service", None)
            if vs is None:
                self.im.reply(incoming.chat_id, "[错误] version_service 未配置")
                return {"status": "rollback_failed"}
            try:
                vs.rollback(template_id=tid, version_number=ver,
                            caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 已回滚模板 {tid} 到版本 {ver}")
                return {"status": "rolled_back", "template_id": tid,
                        "version_number": ver}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "rollback_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "rollback_failed", "reason": str(e)}

        if text.startswith("/template-share "):
            tid = text.split(maxsplit=1)[1].strip()
            ss = getattr(self, "share_service", None)
            if ss is None:
                self.im.reply(incoming.chat_id, "[错误] share_service 未配置")
                return {"status": "share_failed"}
            try:
                ss.share_to_chat(template_id=tid, chat_id=incoming.chat_id,
                                 caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id,
                              f"[成功] 模板 {tid} 已共享到本群")
                return {"status": "shared", "template_id": tid}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "share_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "share_failed", "reason": str(e)}

        # --- 评论查看 / 公共模板 / fork（Phase 7） ---
        if text.startswith("/comments "):
            parts = text.split()
            if len(parts) < 2:
                self.im.reply(incoming.chat_id, "[错误] 用法: /comments <doc_id>")
                return {"status": "comments_failed", "reason": "bad_args"}
            doc_id = parts[1]
            block_id = parts[2] if len(parts) >= 3 else None
            cs = getattr(self, "comment_service", None)
            if cs is None:
                self.im.reply(incoming.chat_id, "[错误] comment_service 未配置")
                return {"status": "comments_failed"}
            rendered = cs.fetch_thread(doc_id=doc_id, block_id=block_id)
            self.im.reply(incoming.chat_id, rendered)
            return {"status": "comments_listed", "doc_id": doc_id,
                    "block_id": block_id}

        if text.startswith("/template-submit-public "):
            tid = text.split(maxsplit=1)[1].strip()
            ps = getattr(self, "public_service", None)
            if ps is None:
                self.im.reply(incoming.chat_id, "[错误] public_service 未配置")
                return {"status": "submit_failed"}
            try:
                ps.submit_for_review(template_id=tid,
                                     actor_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id, f"[成功] 模板 {tid} 已提交公共审核")
                return {"status": "submitted", "template_id": tid}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "submit_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "submit_failed", "reason": str(e)}

        if text.startswith("/template-fork "):
            tid = text.split(maxsplit=1)[1].strip()
            fs = getattr(self, "fork_service", None)
            if fs is None:
                self.im.reply(incoming.chat_id, "[错误] fork_service 未配置")
                return {"status": "fork_failed"}
            try:
                new_id = fs.fork_from_public(
                    source_template_id=tid,
                    actor_open_id=incoming.sender_open_id,
                )
                self.im.reply(incoming.chat_id,
                              f"[成功] 已 fork 模板，新 ID: {new_id}")
                return {"status": "forked", "new_template_id": new_id}
            except PermissionError:
                self.im.reply(incoming.chat_id,
                              "[错误] 仅公共模板可 fork")
                return {"status": "fork_failed", "reason": "not_public"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "fork_failed", "reason": str(e)}

        # --- 评论同步 / 动作应用 / diff / 标签 / 收藏（Phase 8） ---
        if text.startswith("/comments-sync "):
            doc_id = text.split(maxsplit=1)[1].strip()
            ss = getattr(self, "comment_sync_service", None)
            if ss is None:
                self.im.reply(incoming.chat_id, "[错误] comment_sync_service 未配置")
                return {"status": "sync_failed"}
            out = ss.sync(doc_id=doc_id)
            self.im.reply(
                incoming.chat_id,
                f"[成功] 已同步 {doc_id} 评论：拉取 {out['fetched']}"
                f"，新增 {out['new']}，更新 {out['updated']}",
            )
            return {"status": "comments_synced", "doc_id": doc_id, **out}

        if text.startswith("/comment-apply "):
            doc_id = text.split(maxsplit=1)[1].strip()
            ca = getattr(self, "comment_action_service", None)
            if ca is None:
                self.im.reply(incoming.chat_id, "[错误] comment_action_service 未配置")
                return {"status": "apply_failed"}
            out = ca.apply(doc_id=doc_id,
                           caller_open_id=incoming.sender_open_id)
            self.im.reply(
                incoming.chat_id,
                f"[结果] 已应用 {out['applied']} 条，跳过 {out['skipped']} 条"
                f"普通评论，失败 {out['failed']} 条",
            )
            return {"status": "actions_applied", "doc_id": doc_id, **out}

        if text.startswith("/template-diff "):
            parts = text.split()
            if len(parts) < 4:
                self.im.reply(incoming.chat_id,
                              "[错误] 用法: /template-diff <id> <v_a> <v_b>")
                return {"status": "diff_failed", "reason": "bad_args"}
            tid, v_a, v_b = parts[1], int(parts[2]), int(parts[3])
            ds = getattr(self, "diff_service", None)
            if ds is None:
                self.im.reply(incoming.chat_id, "[错误] diff_service 未配置")
                return {"status": "diff_failed"}
            try:
                rendered = ds.render(ds.diff(template_id=tid, v_a=v_a, v_b=v_b))
                self.im.reply(incoming.chat_id, rendered)
                return {"status": "diff_rendered", "template_id": tid}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "diff_failed", "reason": str(e)}

        if text.startswith("/template-tag "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                self.im.reply(incoming.chat_id,
                              "[错误] 用法: /template-tag <id> <tag>")
                return {"status": "tag_failed", "reason": "bad_args"}
            tid, tag = parts[1], parts[2]
            ts = getattr(self, "tag_service", None)
            if ts is None:
                self.im.reply(incoming.chat_id, "[错误] tag_service 未配置")
                return {"status": "tag_failed"}
            try:
                normalized = ts.attach(
                    template_id=tid, tag=tag,
                    caller_open_id=incoming.sender_open_id,
                )
                self.im.reply(incoming.chat_id,
                              f"[成功] 模板 {tid} 已打标签 {normalized}")
                return {"status": "tagged", "template_id": tid, "tag": normalized}
            except PermissionError:
                self.im.reply(incoming.chat_id, "[错误] 您不是模板所有者")
                return {"status": "tag_failed", "reason": "not_owner"}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "tag_failed", "reason": str(e)}

        if text.startswith("/template-favorite "):
            tid = text.split(maxsplit=1)[1].strip()
            fs = getattr(self, "favorite_service", None)
            if fs is None:
                self.im.reply(incoming.chat_id, "[错误] favorite_service 未配置")
                return {"status": "favorite_failed"}
            try:
                fs.favorite(template_id=tid,
                            caller_open_id=incoming.sender_open_id)
                self.im.reply(incoming.chat_id, f"[成功] 已收藏模板 {tid}")
                return {"status": "favorited", "template_id": tid}
            except ValueError as e:
                self.im.reply(incoming.chat_id, f"[错误] {e}")
                return {"status": "favorite_failed", "reason": str(e)}

        if text == "/template-favorites":
            fs = getattr(self, "favorite_service", None)
            if fs is None:
                self.im.reply(incoming.chat_id, "[错误] favorite_service 未配置")
                return {"status": "favorites_failed"}
            tpls = fs.list_favorites(incoming.sender_open_id)
            ids = [getattr(t, "template_id", t) for t in tpls]
            self.im.reply(incoming.chat_id,
                          f"我的收藏: {', '.join(ids) or '(无)'}")
            return {"status": "favorites_listed", "templates": ids}

        # --- 融合检索（Phase 9） ---
        if text.startswith("/template-find"):
            parts = text.split()
            if len(parts) < 2:
                self.im.reply(
                    incoming.chat_id,
                    "[错误] 用法: /template-find <关键词> [#标签]")
                return {"status": "find_failed", "reason": "bad_args"}
            tags = [p.lstrip("#").lower() for p in parts[1:] if p.startswith("#")]
            query_tokens = [p for p in parts[1:] if not p.startswith("#")]
            query = " ".join(query_tokens)
            find_tag = tags[0] if tags else None
            us = getattr(self, "unified_search_service", None)
            if us is None:
                self.im.reply(incoming.chat_id,
                              "[错误] unified_search_service 未配置")
                return {"status": "find_failed"}
            results = us.search(query=query, tag=find_tag, limit=10)
            self.im.reply(incoming.chat_id, us.render(results))
            return {"status": "find_rendered", "count": len(results)}

        # 非本批指令：交回调用方走正常流程
        return None
