#!/usr/bin/env python
"""容器内执行 harness（Phase 13 T2）：跑用户代码文件，输出 stdout + 末表达式 repr。

输出协议（宿主机 KernelPool.exec_code 解析）：
- 退出码 0：stdout = 用户 stdout + "\\n###RESULT###\\n" + repr(末顶层表达式值)
- 退出码非 0：用户异常自然抛出（traceback 落 stderr），已捕获 stdout 先冲刷

SENTINEL 与 orchestrator/executor/kernel_manager.py 的 _SENTINEL 保持一致。
"""
import ast
import contextlib
import io
import sys

SENTINEL = "###RESULT###"


def main() -> int:
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    # 拆出顶层最后一个纯表达式语句单独 eval（REPL 语义）
    body = list(tree.body)
    last_expr = None
    if body and isinstance(body[-1], ast.Expr):
        last_expr = ast.Expression(body[-1].value)
        body = body[:-1]
    mod = ast.Module(body=body, type_ignores=[])

    buf = io.StringIO()
    g: dict = {"__name__": "__main__"}
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(mod, path, "exec"), g)  # noqa: S102 沙箱内执行用户代码
            result = (
                eval(compile(last_expr, path, "eval"), g)  # noqa: S102
                if last_expr is not None else None
            )
    except BaseException:
        # 用户异常：先冲刷已捕获 stdout 再抛（退出码非 0，traceback 落 stderr）
        sys.stdout.write(buf.getvalue())
        raise

    sys.stdout.write(buf.getvalue())
    sys.stdout.write(f"\n{SENTINEL}\n")
    sys.stdout.write(repr(result) if result is not None else "None")
    return 0


if __name__ == "__main__":
    sys.exit(main())
