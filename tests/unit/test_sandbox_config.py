from orchestrator.executor.sandbox import DockerSandbox, DockerSandboxConfig


def test_config_from_settings():
    from config.settings import Settings
    s = Settings  # type: ignore
    cfg = DockerSandboxConfig(
        image="feishu-research-agent/kernel:latest",
        cpu_limit=1.0,
        memory_limit="512m",
        pids_limit=64,
        network_mode="none",
        workspace_path="/tmp/x",
    )
    assert cfg.image == "feishu-research-agent/kernel:latest"
    assert cfg.cpu_limit == 1.0
    assert cfg.memory_limit == "512m"
    assert cfg.pids_limit == 64
    assert cfg.network_mode == "none"


def test_sandbox_docker_args_no_network():
    cfg = DockerSandboxConfig(
        image="x", cpu_limit=1.0, memory_limit="512m",
        pids_limit=64, network_mode="none", workspace_path="/tmp/x",
    )
    args = DockerSandbox._build_docker_args(cfg, container_name="test_c")
    # argparse 模式相邻两元素分别携带 flag 与 value
    i = args.index("--network")
    assert args[i + 1] == "none"
    i = args.index("--cpus")
    assert args[i + 1] == "1.0"
    i = args.index("--memory")
    assert args[i + 1] == "512m"
    i = args.index("--pids-limit")
    assert args[i + 1] == "64"
    assert "--cap-drop=ALL" in args
    assert "--read-only" in args
    assert "--security-opt=no-new-privileges" in args
    assert "test_c" in args


def test_sandbox_docker_args_with_bridge():
    cfg = DockerSandboxConfig(
        image="x", cpu_limit=1.0, memory_limit="512m",
        pids_limit=64, network_mode="bridge", workspace_path="/tmp/x",
    )
    args = DockerSandbox._build_docker_args(cfg, container_name="c2")
    i = args.index("--network")
    assert args[i + 1] == "bridge"


def test_sandbox_exec_input_text_passthrough():
    """T2：input_text 经 stdin 透传且自动加 -i；无 input 不加 -i。"""
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    cfg = DockerSandboxConfig(
        image="x", cpu_limit=1.0, memory_limit="512m",
        pids_limit=64, network_mode="none", workspace_path="/tmp/x",
    )
    sb = DockerSandbox(cfg, run_subprocess=fake_run)

    sb.exec("c1", ["sh", "-c", "cat > /tmp/a.py"],
            input_text="print(1)", timeout_sec=15)
    args, kwargs = calls[0]
    assert args[:3] == ["docker", "exec", "-i"]  # 有 stdin 才加 -i
    assert kwargs["input"] == "print(1)"
    assert kwargs["timeout"] == 15

    sb.exec("c1", ["python", "/opt/run_user.py", "/tmp/a.py"], timeout_sec=60)
    args, kwargs = calls[1]
    assert args[:2] == ["docker", "exec"]
    assert args[2] == "c1"  # 无 -i
    assert kwargs["input"] is None
