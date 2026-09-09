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
import re
import subprocess
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


class BioRunError(Exception):
    """bio 容器执行失败（含 error_code 供 ToolResult 透传）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def compute_dataset_id(abs_path: str) -> str:
    """dataset_ref = sha1(绝对路径 + 文件大小)[:12]（spec §3.3 幂等键）。

    同路径同大小二次分析复用 workspace；文件变化后 id 变化（视为新数据集）。
    文件不存在抛 BioRunError（对齐 compute_dataset_id_dir 的 is_dir 检查）。
    """
    if not os.path.isfile(abs_path):
        raise BioRunError(
            "SC_FILE_NOT_FOUND", f"data file not found: {abs_path}")
    st = os.stat(abs_path)
    digest = hashlib.sha1(
        f"{os.path.realpath(abs_path)}:{st.st_size}".encode("utf-8")
    ).hexdigest()
    return digest[:12]


def compute_dataset_id_dir(abs_dir: str) -> str:
    """目录数据集（如 spaceranger 输出）幂等键：聚合 hash 目录内容。

    递归收集（相对路径, 文件大小, mtime_ns）排序后聚合 sha1[:12]——
    任一文件增删/改内容（大小或 mtime 变化）即换 id（Phase 21 spec §3）。
    目录不存在抛 BioRunError。
    """
    d = Path(abs_dir)
    if not d.is_dir():
        raise BioRunError(
            "SC_FILE_NOT_FOUND", f"data dir not found: {abs_dir}")
    items = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            items.append(f"{p.relative_to(d).as_posix()}:{st.st_size}:{st.st_mtime_ns}")
    digest = hashlib.sha1(
        f"{os.path.realpath(abs_dir)}|".encode("utf-8")
        + "|".join(items).encode("utf-8")).hexdigest()
    return digest[:12]


def parse_gene_list(genes: Any) -> list[str]:
    """宽松解析 genes 参数：接受 list / repr 字符串 / 单基因名字符串。

    planner 偶发把 list 参数序列化成 Python repr 串（"['A', 'B']"，
    Phase 21 真机发现，与 write_doc blocks 同源问题）——literal_eval 还原；
    纯字符串视为单基因名包装为 list；其他类型原样返回由 schema 校验兜底。
    """
    import ast

    if genes is None:
        return []
    if isinstance(genes, list):
        return genes
    if isinstance(genes, str):
        try:
            v = ast.literal_eval(genes)
            if isinstance(v, list):
                return [str(x) for x in v]
        except (ValueError, SyntaxError):
            pass
        return [genes] if genes.strip() else []
    return cast(list[str], genes)


def touch_last_access(workspace_root: str, dataset_id: Any) -> None:
    """Phase 23：GC 打点——更新 <workspace_root>/<dataset_id>/.last_access。

    best-effort：dataset_id 为空或非 12hex（dataset_ref 来自 LLM/planner，
    正则校验挡路径穿越）、目录不存在（load 首跑目录由容器内脚本新建）、
    IO 异常均仅 warning/静默，绝不阻断任务。
    """
    if not dataset_id or not re.fullmatch(r"[0-9a-f]{12}", str(dataset_id)):
        return
    try:
        d = Path(workspace_root) / str(dataset_id)
        if d.is_dir():
            (d / ".last_access").touch()
    except OSError as e:
        logger.warning("gc touch failed for %s: %s", dataset_id, e)


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

    def run(self, script: str, args: dict[str, Any], *,
            timeout_sec: int | None = None,
            mounts: list[tuple[str, str]] | None = None,
            image: str | None = None, script_dir: str = "/opt/sc_tools",
            gpus: bool = False) -> dict[str, Any]:
        """跑 <script_dir>/<script>.py，返回 stdout JSON dict。

        mounts: 额外 (主机目录, 容器目录) 挂载（sc_load 的数据目录）。
        image/script_dir: 覆盖实例默认镜像与脚本目录——st_* 空间转录组
        工具传 image=st 镜像 + script_dir=/opt/st_tools（Phase 21 spec §3）。
        gpus=True 时 docker run 带 --gpus all（GPU 镜像，Phase 25）。
        超时/非零退出/JSON 解析失败 → BioRunError。
        """
        # Phase 23：GC 打点（best-effort，读引用也算"最近使用"）
        touch_last_access(self.workspace_root, args.get("dataset_id"))
        cmd = ["docker", "run", "--rm"]
        if gpus:
            cmd += ["--gpus", "all"]
        cmd += ["-i", "--network", "none",
                "--cpus", self.cpus, "--memory", self.memory,
                "-v", f"{self.workspace_root}:/ws"]
        for host_dir, container_dir in (mounts or []):
            cmd += ["-v", f"{host_dir}:{container_dir}:ro"]
        cmd += [image or self.image, "python", f"{script_dir}/{script}.py"]

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
            # 脚本 fail() 的输出模式是 stdout JSON + exit 1——先尝试解析
            # stdout 的脚本级错误（透传 error_code），失败才归为执行崩溃
            # （Phase 21 真机发现：此前 fail() 错误被吞成空 message）
            try:
                out = json.loads(proc.stdout)
            except (json.JSONDecodeError, TypeError):
                out = None
            if isinstance(out, dict) and out.get("ok") is False \
                    and out.get("error_code"):
                logger.warning(
                    "bio script %s failed rc=%s: %s %s",
                    script, proc.returncode,
                    out.get("error_code"), out.get("error_message", ""))
                raise BioRunError(
                    str(out.get("error_code")),
                    str(out.get("error_message", "unknown script error")))
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
        return cast(dict[str, Any], out)
