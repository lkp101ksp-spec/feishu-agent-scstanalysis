"""挂账② registry 热刷新真机验证（install → register → invoke → 改文件
→ reload_skill → 再 invoke 全链，全程不重启 registry 进程对象）。

用法：.venv\\Scripts\\python.exe scripts\\_verify_skill_hot_reload.py
在生产 skills/ 下创建临时 skill `hotreloadprobe`，验证后自动清理。
退出码 0=PASS，2=FAIL（清理仍会尽力执行）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.coding.skill_installer import SkillInstaller  # noqa: E402
from orchestrator.coding.skill_loader import SkillLoader  # noqa: E402
from orchestrator.tools.tool_registry import (  # noqa: E402
    ToolNotFoundError,
    ToolRegistry,
)

PROBE = "hotreloadprobe"
TOOL = "probe_echo"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def _files(version: str) -> dict[str, str]:
    """生成 probe skill 三件套（version 决定 stdout 前缀，v1/v2 区分版本）。"""
    return {
        "SKILL.md": (f"---\nname: {PROBE}\ndescription: 热刷新验证探针\n---\n"
                     "正文。\n"),
        "tools.yaml": (
            "tools:\n"
            f"  - name: {TOOL}\n"
            "    description: echo probe\n"
            f"    command: [\"{PY.replace(chr(92), chr(92)*2)}\", \"run.py\"]\n"
            "    timeout_sec: 30\n"
            "    parameters:\n"
            "      type: object\n"
            "      properties:\n"
            "        text: {type: string}\n"),
        "run.py": ("import argparse\n"
                   "p = argparse.ArgumentParser()\n"
                   "p.add_argument('--text', default='')\n"
                   "a = p.parse_args()\n"
                   f"print('{version}:' + a.text)\n"),
    }


def main() -> int:
    """全链验证主流程：任一断言失败即 FAIL，finally 清理探针目录。"""
    skills_dir = ROOT / "skills"
    # 挂账⑥双目录：installer 落点到 skills_installed/（门禁豁免区）
    probe_dir = ROOT / "skills_installed" / PROBE
    if probe_dir.exists():
        print(f"[FAIL] {probe_dir} 已存在，先手动清理")
        return 2
    registry = ToolRegistry()
    try:
        # 1) install v1（走生产 SkillInstaller 校验链）
        inst = SkillInstaller(skills_dir).install(PROBE, _files("v1"))
        assert inst.get("ok"), f"install v1 failed: {inst}"
        # 2) 按生产路径 scan + register
        loader = SkillLoader(skills_dir)
        loader.scan()
        loader.register_tools(registry)
        try:
            spec = registry.get(TOOL)
        except ToolNotFoundError:
            raise AssertionError("v1 tool not registered") from None
        r1 = spec.handler(text="hello")
        assert not r1.error_code, f"v1 invoke error: {r1.error_message}"
        assert "v1:hello" in r1.outputs["stdout"], f"v1 stdout: {r1.outputs}"
        print("[1/3] install+register+invoke v1 OK:", r1.outputs["stdout"].strip())

        # 3) 就地改文件为 v2（模拟 diagnoser/installer 写回），不重建 registry
        (probe_dir / "run.py").write_text(_files("v2")["run.py"],
                                          encoding="utf-8")
        # 4) 热刷新
        rel = SkillLoader(skills_dir).reload_skill(registry, PROBE)
        assert (rel.get("ok") and rel["removed"] == [TOOL]
                and rel["added"] == [TOOL]), rel
        # 5) 同一 registry 对象再 invoke——应见 v2 行为
        try:
            spec2 = registry.get(TOOL)
        except ToolNotFoundError:
            raise AssertionError("v2 tool missing after reload") from None
        r2 = spec2.handler(text="hello")
        assert not r2.error_code, f"v2 invoke error: {r2.error_message}"
        assert "v2:hello" in r2.outputs["stdout"], f"v2 stdout: {r2.outputs}"
        print("[2/3] reload_skill OK:", rel)
        print("[3/3] invoke after reload OK:", r2.outputs["stdout"].strip())
        print("[PASS] 热刷新全链验证通过（registry 未重建，行为 v1→v2 生效）")
        return 0
    except AssertionError as e:
        print(f"[FAIL] {e}")
        return 2
    finally:
        registry.unregister(TOOL)
        shutil.rmtree(probe_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
