"""Docker 沙箱配置 + 容器生命周期（Phase 2）。

- 配置：CPU/内存/PID/网络/挂载点
- 抽象：DockerSandbox.start() / stop() / exec() / get_logs()
- mock 友好：所有 IO 走 subprocess.run，可被 monkeypatch 替换
"""
from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass

from shared.errors import SandboxUnavailableError


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
    def from_settings(cls, settings) -> "DockerSandboxConfig":
        return cls(
            image=settings.docker_image,
            cpu_limit=settings.docker_cpu_limit,
            memory_limit=settings.docker_memory_limit,
            pids_limit=settings.docker_pids_limit,
            network_mode=settings.docker_network_mode,
            workspace_path=settings.sandbox_workspace_root,
        )


class DockerSandbox:
    def __init__(self, config: DockerSandboxConfig, *, run_subprocess=subprocess.run) -> None:
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
        try:
            self._run(["docker", "kill", container_name], capture_output=True, timeout=timeout_sec)
        except Exception:
            self._run(
                ["docker", "rm", "-f", container_name], capture_output=True, timeout=timeout_sec
            )

    def exec(self, container_name: str, cmd: list[str], *, timeout_sec: int = 60):
        return self._run(
            ["docker", "exec", container_name] + cmd,
            capture_output=True,
            text=True,
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
