"""ASTGuard：P0 硬阻塞层。

定位：弱检测层（给更友好的拒绝原因 + 审计附加证据）。
真正的安全边界由 Docker 沙箱兜底。

P0 命中即拒绝：
- os.system / os.popen
- subprocess.run / Popen / call / check_output
- socket.socket / create_connection
- ctypes.CDLL / windll / cdll
"""
from __future__ import annotations

import ast

from shared.errors import ToolBlockedError


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

    def check(self, code: str) -> None:
        """命中 P0 抛 ToolBlockedError；通过则静默。"""
        if not code or not code.strip():
            return
        try:
            tree = ast.parse(code)
        except SyntaxError:
            raise ToolBlockedError("code has syntax error; cannot validate safety")

        # 1) 收集 `from X import Y` 的别名（防绕过：subprocess.run 可写为 `from subprocess import run; run(...)`）
        from_imports: dict[str, str] = {}  # alias_name -> module_name
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {"os", "subprocess", "socket", "ctypes"}:
                for alias in node.names:
                    name = alias.asname or alias.name
                    from_imports[name] = node.module

        for node in ast.walk(tree):
            # a) Attribute call：os.system / subprocess.run 等
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    module_id = node.func.value.id
                    # 模块可能是 from_imports 别名
                    if module_id in from_imports:
                        module_id = from_imports[module_id]
                    key = (module_id, node.func.attr)
                    if key in self.BLOCKED_CALLS:
                        raise ToolBlockedError(
                            f"P0 blocked call: {key[0]}.{key[1]} at line {node.lineno}"
                        )
            # b) Name 直调：`from subprocess import run; run([...])`
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in from_imports:
                    module_id = from_imports[node.func.id]
                    # 这种形式 `run(...)` 我们无法静态判定它一定是 P0 函数
                    # （同名合法函数可能存在），但为了安全起见，P0 模块
                    # 的导入别名一律视为高风险，命中即拒绝。
                    raise ToolBlockedError(
                        f"P0 blocked module import: {module_id} (alias {node.func.id}) at line {node.lineno}"
                    )