"""Phase 20：bio 短命容器执行器（spec §3.2）。

docker run --rm --network none --cpus/--memory 限额
  -v <数据目录>:/data:ro -v <bio_workspace_root>:/ws
  bio镜像 python /opt/sc_tools/<script>.py  < stdin=args JSON
stdout 解析 JSON；非 0 / 超时 → 统一 error_code。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class BioRunError(Exception):
    """bio 容器执行失败（含 error_code 供 ToolResult 透传）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def compute_dataset_id(abs_path: str) -> str:
    """dataset_ref = sha1(绝对路径 + 文件大小)[:12]（spec §3.3 幂等键）。

    同路径同大小二次分析复用 workspace；文件变化后 id 变化（视为新数据集）。
    """
    st = os.stat(abs_path)
    digest = hashlib.sha1(
        f"{os.path.realpath(abs_path)}:{st.st_size}".encode("utf-8")
    ).hexdigest()
    return digest[:12]


class BioRunner:
    """短命 bio 容器执行（同步阻塞在工具 handler 内，与 BLAST 同模式）。"""

    def __init__(
        self,
        *,
        image: str,
        workspace_root: str,
        data_roots: list[str],
        timeout_sec: int = 900,
        cpus: str = "4",
        memory: str = "16g",
    ) -> None:
        self.image = image
        self.workspace_root = str(Path(workspace_root).resolve())
        self.data_roots = [str(Path(r).resolve()) for r in data_roots]
        self.timeout_sec = timeout_sec
        self.cpus = cpus
        self.memory = memory

    # --- 路径白名单（spec §5） ---

    def resolve_data_path(self, user_path: str) -> tuple[str, str, str]:
        """校验白名单并返回 (挂载根目录, 容器内 /data 相对路径, 主机绝对路径)。

        realpath 逃逸（../）与白名单外路径均抛 BioRunError(SC_PATH_FORBIDDEN)。
        调用方把挂载根目录只读挂到容器 /data，脚本用相对路径访问。
        """
        if not user_path or not user_path.strip():
            raise BioRunError("INVALID_INPUT", "path is empty")
        host = Path(user_path.strip()).resolve()
        for root in self.data_roots:
            try:
                rel = host.relative_to(root)
            except ValueError:
                continue
            return (root, rel.as_posix(), str(host))
        raise BioRunError(
            "SC_PATH_FORBIDDEN",
            f"path {user_path!r} is outside allowed data roots "
            f"({', '.join(self.data_roots)}); contact admin to extend "
            "bio_data_roots")

    # --- 执行 ---

    def run(self, script: str, args: dict, *, timeout_sec: int | None = None,
            mounts: list[tuple[str, str]] | None = None) -> dict:
        """跑 /opt/sc_tools/<script>.py，返回 stdout JSON dict。

        mounts: 额外 (主机目录, 容器目录) 挂载（sc_load 的数据目录）。
        超时/非零退出/JSON 解析失败 → BioRunError。
        """
        cmd = ["docker", "run", "--rm", "-i", "--network", "none",
               "--cpus", self.cpus, "--memory", self.memory,
               "-v", f"{self.workspace_root}:/ws"]
        for host_dir, container_dir in (mounts or []):
            cmd += ["-v", f"{host_dir}:{container_dir}:ro"]
        cmd += [self.image, "python", f"/opt/sc_tools/{script}.py"]

        timeout = timeout_sec or self.timeout_sec
        try:
            proc = subprocess.run(
                cmd, input=json.dumps(args), capture_output=True,
                text=True, timeout=timeout, encoding="utf-8")
        except subprocess.TimeoutExpired:
            raise BioRunError(
                "SC_TIMEOUT", f"bio script {script} exceeded {timeout}s"
            ) from None
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip()[-500:]
            logger.warning("bio script %s failed rc=%s: %s",
                           script, proc.returncode, stderr_tail)
            raise BioRunError(
                "SC_SCRIPT_FAILED",
                f"bio script {script} exited {proc.returncode}: {stderr_tail}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise BioRunError(
                "SC_OUTPUT_INVALID",
                f"bio script {script} produced non-JSON output: "
                f"{proc.stdout[:200]!r}") from None
        if out.get("ok") is False:
            # 脚本级统一错误（fail() 输出）：透传 error_code
            raise BioRunError(
                out.get("error_code", "SC_SCRIPT_FAILED"),
                out.get("error_message", "unknown script error"))
        return out
