"""lark-cli subprocess 封装。

Phase 1 通过 lark-cli 子进程调用飞书 API，便于本地调试与测试 mock。
Phase 2 评估是否切换到 Feishu Python SDK。
"""
import json
import shlex
import subprocess
from typing import Any


class LarkCLIError(RuntimeError):
    """lark-cli 调用失败（超时 / 退出非 0 / 解析失败 / 二进制缺失）。"""


class LarkCLI:
    """对 lark-cli 命令的薄封装。所有调用通过 subprocess.run，便于测试 mock。"""

    def __init__(self, binary: str = "lark-cli", timeout: int = 30):
        self.binary = binary
        self.timeout = timeout

    def run(self, args: list[str]) -> dict[str, Any]:
        """执行 lark-cli 子命令，解析 stdout JSON 返回。

        异常：
        - LarkCLIError: 退出非 0、超时、二进制缺失、stdout 非 JSON
        """
        cmd = [self.binary, *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise LarkCLIError(
                f"lark-cli timeout after {self.timeout}s: {shlex.join(cmd)}"
            ) from e
        except FileNotFoundError as e:
            raise LarkCLIError(f"lark-cli binary not found: {self.binary}") from e

        if proc.returncode != 0:
            raise LarkCLIError(
                f"lark-cli exit={proc.returncode} stderr={proc.stderr.strip()}"
            )

        out = proc.stdout.strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise LarkCLIError(
                f"lark-cli returned non-json output: {out[:200]}"
            ) from e
