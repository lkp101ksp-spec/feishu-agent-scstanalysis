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
    )
