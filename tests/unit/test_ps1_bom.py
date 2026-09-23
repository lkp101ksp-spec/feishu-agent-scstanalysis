"""PS1 UTF-8 BOM 门禁（2026-09-23 教训#24）。

背景：Windows PowerShell 5.1 将无 BOM 的 UTF-8 脚本按 GBK 解析，
中文字节会吞掉字符串引号导致 parse 崩溃——48h 内 install_guardian_task.ps1
与 check.ps1 两连炸。check.ps1 第 0 步做本地自检；本测试保证 CI（ubuntu）
同样卡住此约定，且 check.ps1 自身崩掉时仍有兜底层。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOM = b"\xef\xbb\xbf"


def _ps1_files() -> list[Path]:
    """枚举门禁范围内的 .ps1：根目录 + scripts/（与 check.ps1 第 0 步同口径）。"""
    return sorted(ROOT.glob("*.ps1")) + sorted((ROOT / "scripts").glob("*.ps1"))


def test_ps1_gate_scope_nonempty() -> None:
    """范围为空说明目录约定漂移，门禁静默失效——必须显式报警。"""
    assert _ps1_files(), "未找到任何 .ps1——BOM 门禁范围失效"


def test_ps1_utf8_bom() -> None:
    """所有门禁内 .ps1 必须以 UTF-8 BOM 开头，否则 PS5.1 按 GBK 解析。"""
    bad = [str(p.relative_to(ROOT)) for p in _ps1_files() if not p.read_bytes().startswith(BOM)]
    assert not bad, f".ps1 缺 UTF-8 BOM: {bad}"
