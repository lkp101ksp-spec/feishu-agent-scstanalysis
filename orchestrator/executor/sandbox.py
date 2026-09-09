"""Docker 沙箱配置 + 容器生命周期（Phase 2）。

- 配置：CPU/内存/PID/网络/挂载点
- 抽象：DockerSandbox.start() / stop() / exec() / get_logs()
- mock 友好：所有 IO 走 subprocess.run，可被 monkeypatch 替换
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Callable, cast

from config.settings import Settings
from shared.errors import SandboxUnavailableError


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """杀整棵进程树：Windows 用 taskkill /F /T，POSIX 杀本体即可。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=10,
        )
    else:
        proc.kill()


def _run_subprocess(args: list[str], *, capture_output: bool = True,
                    text: bool = True, input: str | None = None,
                    timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """subprocess.run 的进程树安全版。

    Windows 下 docker.exe 会派生子进程持有 stdio 管道句柄；subprocess.run 超时
    只杀 docker.exe 本体，管道 EOF 永不到达 → communicate() 挂死（T2 集成测试
    卡死根因）。超时后先 taskkill 杀整棵进程树，再回收输出并重抛 TimeoutExpired。
    """
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            args, cast(float, timeout), output=stdout, stderr=stderr,
        ) from None
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


@dataclass
class DockerSandboxConfig:
    image: str
    cpu_limit: float
    memory_limit: str
    pids_limit: int
    network_mode: str  # "none" | "bridge"
    workspace_path: str  # 主机侧 workspace 根目录
    workspace_dir_name: str = "workspace"
    tmpfs_workspace_mb: int = 512

    @classmethod
    def from_settings(cls, settings: Settings) -> "DockerSandboxConfig":
        return cls(
            image=settings.docker_image,
            cpu_limit=settings.docker_cpu_limit,
            memory_limit=settings.docker_memory_limit,
            pids_limit=settings.docker_pids_limit,
            network_mode=settings.docker_network_mode,
            workspace_path=settings.sandbox_workspace_root,
        )


class DockerSandbox:
    def __init__(
        self,
        config: DockerSandboxConfig,
        *,
        run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = _run_subprocess,
    ) -> None:
        self.config = config
        self._run = run_subprocess

    @staticmethod
    def _build_docker_args(cfg: DockerSandboxConfig, *, container_name: str) -> list[str]:
        return [
            "docker", "run", "-d",
            "--name", container_name,
            "--network", cfg.network_mode,
            "--cpus", str(cfg.cpu_limit),
            "--memory", cfg.memory_limit,
            "--pids-limit", str(cfg.pids_limit),
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:size=64m",
            "--tmpfs", f"/{cfg.workspace_dir_name}:size={cfg.tmpfs_workspace_mb}m",
            "-u", "1000:1000",
            "--restart", "no",
            cfg.image,
        ]

    def start(self, session_id: str) -> str:
        container_name = f"feishu-{session_id}-{uuid.uuid4().hex[:8]}"
        args = self._build_docker_args(self.config, container_name=container_name)
        result = self._run(args, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise SandboxUnavailableError(
                f"docker run failed: rc={result.returncode} stderr={result.stderr[:200]}"
            )
        return container_name

    def stop(self, container_name: str, *, timeout_sec: int = 5) -> None:
        """kill 后必 rm——只 kill 容器会永久残留为 Exited（真机 2026-08-30）。"""
        try:
            self._run(["docker", "kill", container_name], capture_output=True,
                      timeout=timeout_sec)
        except Exception:
            pass  # 已退出/不存在时 kill 报错无所谓，rm -f 兜底清理
        try:
            self._run(["docker", "rm", "-f", container_name], capture_output=True,
                      timeout=timeout_sec)
        except Exception:
            pass

    def exec(self, container_name: str, cmd: list[str], *, timeout_sec: int = 60,
             input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        """docker exec；input_text 经 stdin 透传（T2：用户代码零转义写入）。"""
        return self._run(
            ["docker", "exec"] + (["-i"] if input_text is not None else [])
            + [container_name] + cmd,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout_sec,
        )

    def get_logs(self, container_name: str, *, tail: int = 100) -> str:
        result = self._run(
            ["docker", "logs", "--tail", str(tail), container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout + result.stderr
