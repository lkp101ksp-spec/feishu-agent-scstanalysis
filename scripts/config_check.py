#!/usr/bin/env python
"""Phase 10 T6：飞书联调环境校验器（纯标准库，ADR-0030）。

检查 .env 必填/可选分组、config/*.yaml 冒烟、DATABASE_URL 前缀；
可选 --probe 探 LLM base_url 可达性（3s 超时）、--docker 探 Docker daemon。
输出 JSON（errors/warnings/infos）；exit 0=无 error，2=有 error。

用法：
    python scripts/config_check.py --env-file .env [--probe] [--docker]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 必填组：飞书三件套 + LLM 路由六项 + 数据库
FEISHU_REQUIRED = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK_SECRET"]
LLM_REQUIRED = [
    "LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
    "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL",
]
REQUIRED = FEISHU_REQUIRED + LLM_REQUIRED + ["DATABASE_URL"]
# 可选组：缺了只 warn 不挡启动
OPTIONAL = ["PG_TEST_URL", "COMMENT_SYNC_INTERVAL_SEC", "BIND_DOC_TTL_SEC",
            "GATEWAY_RATE_LIMIT_PER_MIN"]
DB_URL_PREFIXES = ("postgresql://", "postgresql+psycopg://", "sqlite:///")


def load_env_file(path: str | Path) -> dict[str, str]:
    """解析 KEY=VALUE 行（跳过空行/# 注释，去引号）；文件不存在返回空 dict。"""
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def check_yaml_smoke(result: dict, root: Path = ROOT) -> None:
    """config yaml 冒烟：存在、非空、至少一行形如 key: 结构（纯文本启发式，不引 yaml 依赖）。"""
    for name in ("config/feishu.yaml", "config/llm.yaml"):
        p = root / name
        if not p.exists():
            result["warnings"].append(f"{name} 不存在")
            continue
        lines = [
            raw for raw in p.read_text(encoding="utf-8").splitlines()
            if raw.strip() and not raw.strip().startswith("#")
        ]
        if not lines:
            result["warnings"].append(f"{name} 内容为空")
        elif not any(re.match(r"^[A-Za-z0-9_.\-]+\s*:", ln) for ln in lines):
            result["warnings"].append(f"{name} 疑似非 yaml 结构（无 key: 行）")


def check_db_url(env: dict[str, str], result: dict) -> None:
    """DATABASE_URL 前缀白名单：postgresql:// / postgresql+psycopg:// / sqlite:///。"""
    url = env.get("DATABASE_URL", "")
    if url and not url.startswith(DB_URL_PREFIXES):
        result["errors"].append(
            f"DATABASE_URL 前缀非法: {url[:30]}...（允许 {' / '.join(DB_URL_PREFIXES)}）"
        )


def check_llm_reachable(env: dict[str, str], result: dict, timeout: float = 3.0) -> None:
    """逐个探测 LLM base_url；不可达只 warn（内网/VPN 场景常见，不挡启动）。"""
    for key in ("LLM_PRIMARY_BASE_URL", "LLM_FALLBACK_BASE_URL"):
        url = env.get(key)
        if not url:
            continue
        try:
            urllib.request.urlopen(url, timeout=timeout)
            result["infos"].append(f"{key} 可达")
        except urllib.error.HTTPError:
            # 有 HTTP 响应说明服务在线（401/404 等属正常）
            result["infos"].append(f"{key} 在线（返回 HTTP 状态码）")
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            result["warnings"].append(f"{key} 不可达（{e}）：内网/未开 VPN 时可忽略")


def check_docker(result: dict) -> None:
    """探测 Docker daemon（pg 层前置条件，不可用只提示不挡）。"""
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            result["infos"].append(f"Docker daemon 在线（server {proc.stdout.strip()}）")
        else:
            result["warnings"].append("docker 已安装但 daemon 未运行（-m pg 层将 skip）")
    except (OSError, subprocess.TimeoutExpired):
        result["warnings"].append("未检测到 docker 命令（-m pg 层将 skip）")


def validate(
    env: dict[str, str], *,
    probe: bool = False, docker: bool = False, root: Path = ROOT,
) -> dict:
    """主校验入口：返回 {"errors": [], "warnings": [], "infos": []}。"""
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "infos": []}
    for key in REQUIRED:
        if not env.get(key):
            result["errors"].append(f"必填缺失: {key}")
    for key in OPTIONAL:
        if not env.get(key):
            result["warnings"].append(f"可选未设置: {key}（用默认值）")
    check_db_url(env, result)
    check_yaml_smoke(result, root)
    if probe:
        check_llm_reachable(env, result)
    if docker:
        check_docker(result)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：打印 JSON 结果，返回 exit code（0/2）。"""
    parser = argparse.ArgumentParser(description="飞书联调环境校验")
    parser.add_argument("--env-file", default=".env", help="env 文件路径（默认 .env）")
    parser.add_argument("--probe", action="store_true", help="探测 LLM base_url 可达性（3s 超时）")
    parser.add_argument("--docker", action="store_true", help="探测 Docker daemon")
    args = parser.parse_args(argv)

    env = load_env_file(args.env_file)
    result = validate(env, probe=args.probe, docker=args.docker)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
