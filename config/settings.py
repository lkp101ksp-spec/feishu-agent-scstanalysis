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