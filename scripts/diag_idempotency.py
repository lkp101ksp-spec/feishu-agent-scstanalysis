"""幂等重投验证脚本：用已处理过的 message_id 再推一次，确认返回 duplicate。

用法：.venv\\Scripts\\python.exe scripts\\diag_idempotency.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import load_env_file  # noqa: E402

load_env_file()

from gateway.app import run_im_pipeline  # noqa: E402
from gateway.runtime import build_runtime  # noqa: E402

# 从库查到最近一条已处理消息的 message_id
RECENT_MESSAGE_ID = "om_x100b663b6c8a30b4b328c2cae2a345c"
CHAT_ID = "oc_a2a2ee45352c449e617b370b970ab686"
APP_ID = os.environ.get("FEISHU_APP_ID", "cli_aa1a8a41f378dcbc")

# 构造一个与原始事件相同 message_id 的 payload
PAYLOAD = {
    "header": {
        "event_id": "fake_repush_for_idempotency_test",
        "event_type": "im.message.receive_v1",
        "app_id": APP_ID,
    },
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_test_repush"},
            "sender_type": "user",
        },
        "message": {
            "chat_id": CHAT_ID,
            "message_id": RECENT_MESSAGE_ID,  # 关键：复用已处理过的 message_id
            "message_type": "text",
            "chat_type": "p2p",
            "content": '{"text":"幂等重投测试"}',
            "mentions": None,
        },
    },
}


def main() -> None:
    """构造 runtime → 模拟重推 → 打印结果。"""
    rt = build_runtime()
    result = run_im_pipeline(rt.app, APP_ID, PAYLOAD)
    print(f"重推结果: {result}")
    if result.get("status") == "duplicate":
        print("✅ 幂等去重生效：同 message_id 重推被短路，未进入业务处理")
    else:
        print(f"⚠️ 未短路，status={result.get('status')}，可能幂等逻辑有缺口")


if __name__ == "__main__":
    main()
