"""ASTGuard：P0 硬阻塞 + P1/P2 提示。

定位：弱检测层（给更友好的拒绝原因 + 审计附加证据）。
真正的安全边界由 Docker 沙箱兜底。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from shared.errors import ToolBlockedError


@dataclass
class ASTReport:
    """AST 检查结果：blocked + notices。"""
    blocked: bool = False
    notices: list[tuple[str, str, int]] = field(default_factory=list)


class ASTGuard:
    BLOCKED_CALLS: set[tuple[str, str]] = {
        ("os", "system"),
        ("os", "popen"),
        ("subprocess", "run"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("subprocess", "check_output"),
        ("socket", "socket"),
        ("socket", "create_connection"),
        ("ctypes", "CDLL"),
        ("ctypes", "windll"),
        ("ctypes", "cdll"),
    }

    # Phase 6: 直接拦截的 builtin 函数
    BLOCKED_BUILTINS: set[str] = {
        "eval", "exec", "__import__", "compile",
        "globals", "locals", "vars",
    }

    P1_PATTERNS = {  # 网络出口
        ("requests", "get"), ("requests", "post"), ("requests", "put"),
        ("requests", "delete"), ("requests", "patch"),
        ("urllib", "request"), ("urllib.request", "urlopen"),
        ("httpx", "get"), ("httpx", "post"),
        ("aiohttp", "ClientSession"), ("aiohttp", "get"), ("aiohttp", "post"),
    }
    P2_PATTERNS = {  # 文件越界
        ("shutil", "copy"), ("shutil", "move"), ("shutil", "rmtree"),
        ("os", "remove"), ("os", "unlink"), ("os", "rmdir"),
    }

    def check(self, code: str) -> ASTReport:
        """返回 ASTReport；P0 命中时 raise ToolBlockedError。"""
        report = ASTReport()
        if not code or not code.strip():
            return report
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            # 附错误位置 + 注入后 code 头部：上游引用注入产生的语法错误
            # 若只有一句报错，用户/模型无从定位（真机 2026-08-31 b1_tt1）
            raise ToolBlockedError(
                f"code has syntax error; cannot validate safety"
                f"（行{e.lineno}:{e.offset} {e.msg}）"
                f"；code 头部: {code[:160]!r}"
            )
        # 1) 收集 from X import Y 别名（防绕过）
        from_imports: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "os", "subprocess", "socket", "ctypes",
                # 网络 / 文件模块：作为 P1/P2 提示来源
                "requests", "urllib", "urllib.request",
                "httpx", "aiohttp",
                "shutil",
            }:
                for alias in node.names:
                    name = alias.asname or alias.name
                    from_imports[name] = node.module
        for node in ast.walk(tree):
            # P0 (Attribute call) + P1/P2 提示
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    module_id = node.func.value.id
                    if module_id in from_imports:
                        module_id = from_imports[module_id]
                    key = (module_id, node.func.attr)
                    if key in self.BLOCKED_CALLS:
                        raise ToolBlockedError(
                            f"P0 blocked call: {key[0]}.{key[1]} at line {node.lineno}"
                        )
                    if key in self.P1_PATTERNS:
                        report.notices.append(
                            ("P1", f"网络出口 {key[0]}.{key[1]}", node.lineno)
                        )
                    if key in self.P2_PATTERNS:
                        report.notices.append(
                            ("P2", f"文件越界 {key[0]}.{key[1]}", node.lineno)
                        )
            # P0 Name 直调：`from subprocess import run; run([...])`
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                # Phase 6: 直接拦截的 builtin（eval / exec / __import__）
                if node.func.id in self.BLOCKED_BUILTINS:
                    raise ToolBlockedError(
                        f"P0 blocked builtin: {node.func.id} at line {node.lineno}"
                    )
                if node.func.id in from_imports:
                    module_id = from_imports[node.func.id]
                    if module_id in {"os", "subprocess", "socket", "ctypes"}:
                        raise ToolBlockedError(
                            f"P0 blocked module import: {module_id} "
                            f"(alias {node.func.id}) at line {node.lineno}"
                        )
                    # P1/P2 模块的直调：仅提示
                    if module_id in {"requests", "urllib", "urllib.request",
                                      "httpx", "aiohttp"}:
                        report.notices.append(
                            ("P1", f"网络出口 {module_id} (alias {node.func.id})",
                             node.lineno)
                        )
                    if module_id == "shutil":
                        report.notices.append(
                            ("P2", f"文件越界 {module_id} (alias {node.func.id})",
                             node.lineno)
                        )
            # P2: open() 路径检查
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open" and len(node.args) >= 1:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if not arg.value.startswith("/workspace/"):
                            report.notices.append(
                                ("P2", f"文件路径越界 {arg.value}", node.lineno)
                            )
        return report
