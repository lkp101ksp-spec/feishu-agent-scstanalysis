"""Phase 10 T6：scripts/config_check.py 校验器单测（纯标准库实现，ADR-0030）。"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# scripts/ 非包：按文件路径加载模块
_SPEC = importlib.util.spec_from_file_location(
    "config_check", Path(__file__).resolve().parents[2] / "scripts" / "config_check.py"
)
cc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cc)

ROOT = Path(__file__).resolve().parents[2]

# 全齐环境（base_url 用不可达地址，验证 probe 关闭时不联网）
FULL_ENV = {
    "FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "sec", "FEISHU_WEBHOOK_SECRET": "wh",
    "LLM_PRIMARY_BASE_URL": "http://10.255.255.1:1/v1",
    "LLM_PRIMARY_API_KEY": "k", "LLM_PRIMARY_MODEL": "m",
    "LLM_FALLBACK_BASE_URL": "http://10.255.255.1:2/v1",
    "LLM_FALLBACK_API_KEY": "k", "LLM_FALLBACK_MODEL": "m",
    "DATABASE_URL": "sqlite:///./dev.db",
}


def test_all_missing_reports_errors():
    """必填全缺：errors 覆盖 10 个必填键。"""
    result = cc.validate({})
    assert len(result["errors"]) == len(cc.REQUIRED) == 10
    assert any("FEISHU_APP_ID" in e for e in result["errors"])
    assert any("DATABASE_URL" in e for e in result["errors"])


def test_all_present_zero_errors():
    """必填全齐 + 合法 DATABASE_URL：errors 为空。"""
    result = cc.validate(dict(FULL_ENV))
    assert result["errors"] == []


def test_bad_yaml_warns():
    """yaml 坏（无 key: 结构）：仅 warn 不 error。

    本机系统 Temp 受限（tmp_path 夹具 PermissionError），
    用 tempfile 在项目根下建临时目录替代。
    """
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT) as td:
        td = Path(td)
        (td / "config").mkdir()
        (td / "config" / "feishu.yaml").write_text("<<<not yaml>>>", encoding="utf-8")
        (td / "config" / "llm.yaml").write_text("a: 1", encoding="utf-8")
        result = {"errors": [], "warnings": [], "infos": []}
        cc.check_yaml_smoke(result, root=td)
    assert any("疑似非 yaml" in w for w in result["warnings"])
    assert result["errors"] == []


def test_bad_db_prefix_is_error():
    """DATABASE_URL 前缀非法：记 error。"""
    env = dict(FULL_ENV, DATABASE_URL="http://mysql:3306/x")
    result = cc.validate(env)
    assert any("DATABASE_URL 前缀非法" in e for e in result["errors"])


def test_optional_missing_only_warning():
    """可选组缺失：只 warn，不影响 errors。"""
    result = cc.validate(dict(FULL_ENV))
    assert result["errors"] == []
    assert any("PG_TEST_URL" in w for w in result["warnings"])


def test_probe_off_no_network():
    """probe=False：不发起任何可达性探测（不可达地址不产生条目）。"""
    result = cc.validate(dict(FULL_ENV), probe=False)
    joined = result["errors"] + result["warnings"] + result["infos"]
    assert not any("不可达" in m or "可达" in m for m in joined)


def test_cli_exit_codes():
    """CLI exit code：全缺=2，全齐=0（tempfile 替代受限的 tmp_path）。"""
    import tempfile
    script = str(ROOT / "scripts" / "config_check.py")
    with tempfile.TemporaryDirectory(dir=ROOT) as td:
        bad = Path(td) / "bad.env"
        bad.write_text("FEISHU_APP_ID=only\n", encoding="utf-8")
        good = Path(td) / "good.env"
        good.write_text(
            "\n".join(f"{k}={v}" for k, v in FULL_ENV.items()), encoding="utf-8"
        )
        rc_bad = subprocess.run(
            [sys.executable, script, "--env-file", str(bad)], capture_output=True
        ).returncode
        rc_good = subprocess.run(
            [sys.executable, script, "--env-file", str(good)], capture_output=True
        ).returncode
    assert rc_bad == 2
    assert rc_good == 0


def test_probe_unreachable_warns():
    """probe 开启 + 不可达地址：记 warn（非 error）。"""
    result = {"errors": [], "warnings": [], "infos": []}
    cc.check_llm_reachable(
        {"LLM_PRIMARY_BASE_URL": "http://127.0.0.1:9/v1"}, result, timeout=2
    )
    assert any("不可达" in w for w in result["warnings"])
    assert result["errors"] == []
