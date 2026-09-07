"""测试会话级环境兜底：无 .env 的环境（CI）为 load_settings 必填键注入哑值。

仅当仓库根目录不存在 .env 时生效，且一律 setdefault——
本地真实 .env / 显式环境变量优先，测试内 monkeypatch.setenv/delenv 不受影响。
"""
import os
from pathlib import Path

_DUMMY_ENV = {
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "test-secret",
    "FEISHU_WEBHOOK_SECRET": "test-webhook",
    "LLM_PRIMARY_BASE_URL": "http://127.0.0.1:1",
    "LLM_PRIMARY_API_KEY": "test-key",
    "LLM_PRIMARY_MODEL": "test-model",
    "LLM_FALLBACK_BASE_URL": "http://127.0.0.1:1",
    "LLM_FALLBACK_API_KEY": "test-key",
    "LLM_FALLBACK_MODEL": "test-model",
    "DATABASE_URL": "sqlite:///:memory:",
}

if not (Path(__file__).resolve().parent.parent / ".env").exists():
    for _key, _value in _DUMMY_ENV.items():
        os.environ.setdefault(_key, _value)
