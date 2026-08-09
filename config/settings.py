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


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_settings() -> Settings:
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
    )