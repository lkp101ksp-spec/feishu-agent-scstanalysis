"""评论真机诊断：列评论（校验官方结构适配）+ 发一条回执到指定评论。

用法：
  .venv\\Scripts\\python.exe scripts\\diag_comments.py list <doc_id>
  .venv\\Scripts\\python.exe scripts\\diag_comments.py reply <doc_id> <comment_id> <text>
"""
import sys

sys.path.insert(0, ".")

from config.settings import load_env_file  # noqa: E402

load_env_file()

import lark_oapi as lark  # noqa: E402

from feishu_adapter.bot_info import get_bot_open_id  # noqa: E402
from feishu_adapter.comment_client import CommentClient  # noqa: E402
from orchestrator.tools.bio.rate_limiter import RateLimiter  # noqa: E402


def main() -> None:
    """按 argv 分发 list / reply。"""
    import os
    sdk = (lark.Client.builder()
           .app_id(os.environ["FEISHU_APP_ID"])
           .app_secret(os.environ["FEISHU_APP_SECRET"])
           .log_level(lark.LogLevel.INFO)
           .build())
    client = CommentClient(sdk_client=sdk,
                           rate_limiter=RateLimiter(rate=3.0, per_sec=1.0))
    print("bot open_id:", get_bot_open_id(sdk))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for c in client.list_comments(doc_id=sys.argv[2]):
            print(c)
    elif cmd == "reply":
        out = client.reply_comment(file_token=sys.argv[2],
                                   comment_id=sys.argv[3], text=sys.argv[4])
        print("replied:", out)


if __name__ == "__main__":
    main()
