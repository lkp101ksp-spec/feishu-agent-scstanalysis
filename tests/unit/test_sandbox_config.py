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
