"""从 yaml + 环境变量构造 Settings 单例。"""
import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    webhook_secret: str


@dataclass(frozen=True)
class LLMSettings:
    primary_base_url: str
    primary_api_key: str
    primary_model: str
    fallback_base_url: str
    fallback_api_key: str
    fallback_model: str
    max_retries: int


@dataclass(frozen=True)
class Settings:
    feishu: FeishuSettings
    llm: LLMSettings
    database_url: str
    bind_doc_ttl_sec: int
    rate_limit_per_min: int
    # === Phase 2: Executor ===
    docker_image: str = "feishu-research-agent/kernel:latest"
    docker_cpu_limit: float = 1.0
    docker_memory_limit: str = "512m"
    docker_pids_limit: int = 64
    docker_network_mode: str = "none"
    kernel_idle_timeout_sec: int = 1800
    kernel_exec_timeout_sec: int = 60
    sandbox_workspace_root: str = "/var/lib/feishu-agent/sessions"
    # === Phase 2: Planner ===
    max_concurrent_nodes: int = 4
    node_default_max_retries: int = 1
    context_token_budget: int = 200_000
    # === Phase 2: Approval ===
    approval_default_ttl_sec: int = 1800
    approval_hmac_secret: str = "phase2-dev-secret-change-me"
    # === Phase 2: Drive ===
    drive_max_file_size_mb: int = 500
    drive_upload_chunk_size_mb: int = 4
    drive_inline_threshold_kb: int = 1024
    # === Phase 3: 上下文压缩 ===
    context_compress_trigger_ratio: float = 0.8
    context_freeze_trigger_ratio: float = 0.95
    context_preserve_recent_n: int = 5
    # === Phase 3: 循环上限 ===
    loop_max_iterations_default_while: int = 10
    loop_max_iterations_default_for: int = 100
    # === Phase 3: bind_doc 续期 ===
    bind_doc_renew_threshold_sec: int = 300
    bind_doc_renew_card_interval_sec: int = 60
    # === Phase 4 MVP: BLAST ===
    blast_api_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    blast_rate_per_sec: float = 3.0
    blast_timeout_sec: int = 30
    blast_max_hits_default: int = 5
    # === Phase 9: 评论自动同步 ===
    comment_sync_interval_sec: int = 300
    # 全量评论提醒开关：False 只推含指令的评论（防噪音默认）；
    # True 时所有新评论都推 IM 通知
    comment_notify_all: bool = False
    # === Phase 12: 研究任务执行 ===
    # /research 后台执行整体 wall-clock 上限（秒）
    research_task_timeout_sec: int = 300
    # LLM 单次调用超时（秒）：DAG 规划 prompt 大、推理重，30s 真机会 ReadTimeout
    llm_timeout_sec: int = 120
    # === Phase 14: L2 写回审批流 ===
    # 研究任务写回模式：card_confirm（卡片确认后写入）/ bind_scope（绑定即授权直写）
    research_writeback_approval: str = "card_confirm"
    # 审批卡片等待时长（秒），超时按拒绝处理
    research_approval_timeout_sec: int = 600
    # run_python 代码级失败（运行时异常/语法被拦）LLM 自愈重试上限（0 关闭）
    node_repair_max_retries: int = 1
    # === Phase 17: 节点级 L2 审批 ===
    # research 链路开放 write_doc 节点（执行前卡片确认）；False 时回到
    # Phase 14 行为（L2 整体不可规划 + 收尾整体写回）
    research_allow_node_l2: bool = True
    # === Phase 16: 安全加固 ===
    # 沙箱容器空闲清扫线程间隔（秒，0 关闭；空闲超时仍由 kernel_idle_timeout_sec 决定）
    kernel_sweep_interval_sec: int = 300
    # 工具禁用名单（逗号分隔，如 "blast_search,run_python"；planner 不可见 + 执行层拒绝）
    disabled_tools: str = ""
    # === Phase 20: 单细胞分析（bio 容器） ===
    # bio 镜像 tag（GPU 后续换 bio:gpu-latest，spec §8）
    bio_image: str = "feishu-research-agent/bio:cpu-latest"
    # dataset workspace 根目录（h5ad 中间产物/图落此处，主机路径）
    bio_workspace_root: str = "./bio_workspace"
    # 数据白名单根目录（逗号分隔；sc_load 的 path 必须位于其一之内）
    bio_data_roots: str = ""
    # bio 脚本容器超时（秒）；plan 含 sc_* 时 research wall-clock 放大到 research_sc_timeout_sec
    bio_script_timeout_sec: int = 900
    research_sc_timeout_sec: int = 3600
    # bio 容器资源限额（docker --cpus/--memory）
    bio_cpus: str = "4"
    bio_memory: str = "16g"


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_env_file(path: str = ".env") -> None:
    """Phase 10 联调补充：存在 .env 时注入未设置的环境变量。

    纯标准库实现（不引 python-dotenv）；已存在的环境变量优先
    （setdefault 语义，保证测试 monkeypatch 与部署注入不受影响）。
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(
                key.strip(), value.strip().strip('"').strip("'")
            )


def load_settings() -> Settings:
    load_env_file()
    feishu_cfg = _load_yaml("config/feishu.yaml")
    llm_cfg = _load_yaml("config/llm.yaml")
    return Settings(
        feishu=FeishuSettings(
            app_id=os.environ[feishu_cfg["app_id_env"]],
            app_secret=os.environ[feishu_cfg["app_secret_env"]],
            webhook_secret=os.environ[feishu_cfg["webhook_secret_env"]],
        ),
        llm=LLMSettings(
            primary_base_url=os.environ[llm_cfg["router"]["primary"]["base_url_env"]],
            primary_api_key=os.environ[llm_cfg["router"]["primary"]["api_key_env"]],
            primary_model=os.environ[llm_cfg["router"]["primary"]["model_env"]],
            fallback_base_url=os.environ[llm_cfg["router"]["fallback"]["base_url_env"]],
            fallback_api_key=os.environ[llm_cfg["router"]["fallback"]["api_key_env"]],
            fallback_model=os.environ[llm_cfg["router"]["fallback"]["model_env"]],
            max_retries=llm_cfg["router"]["max_retries"],
        ),
        database_url=os.environ["DATABASE_URL"],
        bind_doc_ttl_sec=int(os.environ.get("BIND_DOC_TTL_SEC", "1800")),
        rate_limit_per_min=int(os.environ.get("GATEWAY_RATE_LIMIT_PER_MIN", "60")),
        bind_doc_renew_threshold_sec=int(
            os.environ.get("BIND_DOC_RENEW_THRESHOLD_SEC", "300")),
        bind_doc_renew_card_interval_sec=int(
            os.environ.get("BIND_DOC_RENEW_CARD_INTERVAL_SEC", "60")),
        # 全量评论提醒开关：COMMENT_NOTIFY_ALL=1/true 开启（默认关）
        comment_notify_all=os.environ.get("COMMENT_NOTIFY_ALL", "0").lower()
        in ("1", "true", "yes"),
        # Phase 12：研究任务执行配置
        research_task_timeout_sec=int(
            os.environ.get("RESEARCH_TASK_TIMEOUT_SEC", "300")),
        max_concurrent_nodes=int(
            os.environ.get("MAX_CONCURRENT_NODES", "4")),
        llm_timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", "120")),
        # Phase 14：写回审批（RESEARCH_WRITEBACK_APPROVAL=bind_scope 关闭卡片确认）
        research_writeback_approval=os.environ.get(
            "RESEARCH_WRITEBACK_APPROVAL", "card_confirm"),
        research_approval_timeout_sec=int(
            os.environ.get("RESEARCH_APPROVAL_TIMEOUT_SEC", "600")),
        node_repair_max_retries=int(
            os.environ.get("NODE_REPAIR_MAX_RETRIES", "1")),
        # Phase 17：节点级 L2 审批开关（RESEARCH_ALLOW_NODE_L2=0 关闭）
        research_allow_node_l2=os.environ.get(
            "RESEARCH_ALLOW_NODE_L2", "1").lower() not in ("0", "false", "no"),
        # Phase 13 T2：沙箱配置外化（DOCKER_*/KERNEL_*）
        docker_image=os.environ.get(
            "DOCKER_IMAGE", "feishu-research-agent/kernel:latest"),
        docker_cpu_limit=float(os.environ.get("DOCKER_CPU_LIMIT", "1.0")),
        docker_memory_limit=os.environ.get("DOCKER_MEMORY_LIMIT", "512m"),
        docker_pids_limit=int(os.environ.get("DOCKER_PIDS_LIMIT", "64")),
        docker_network_mode=os.environ.get("DOCKER_NETWORK_MODE", "none"),
        kernel_idle_timeout_sec=int(
            os.environ.get("KERNEL_IDLE_TIMEOUT_SEC", "1800")),
        kernel_exec_timeout_sec=int(
            os.environ.get("KERNEL_EXEC_TIMEOUT_SEC", "60")),
        # Phase 16：清扫间隔（0 关闭）+ 工具禁用名单
        kernel_sweep_interval_sec=int(
            os.environ.get("KERNEL_SWEEP_INTERVAL_SEC", "300")),
        disabled_tools=os.environ.get("DISABLED_TOOLS", ""),
        # Phase 20：bio 容器（单细胞分析）
        bio_image=os.environ.get(
            "BIO_IMAGE", "feishu-research-agent/bio:cpu-latest"),
        bio_workspace_root=os.environ.get(
            "BIO_WORKSPACE_ROOT", "./bio_workspace"),
        bio_data_roots=os.environ.get("BIO_DATA_ROOTS", ""),
        bio_script_timeout_sec=int(
            os.environ.get("BIO_SCRIPT_TIMEOUT_SEC", "900")),
        research_sc_timeout_sec=int(
            os.environ.get("RESEARCH_SC_TIMEOUT_SEC", "3600")),
        bio_cpus=os.environ.get("BIO_CPUS", "4"),
        bio_memory=os.environ.get("BIO_MEMORY", "16g"),
    )
