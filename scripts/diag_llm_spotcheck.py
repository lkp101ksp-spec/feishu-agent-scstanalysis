"""真 LLM 规划抽查脚本（ut-6）：模拟飞书 IM 消息走完整事件管线。

用 build_runtime() 装配真实组件（真 LLMRouter/Orchestrator/tool_handler），
对每条输入文本构造全新 message_id 的假 im.message.receive_v1 事件，
推入 run_im_pipeline（与 webhook/长连接同一管线），观察真实 LLM 的
意图识别、工具选择与参数填充、以及沙箱执行结果。

/research 路径是 daemon 线程异步执行，进程退出会杀掉任务线程，因此
本脚本推完消息后按 message_id 轮询 tasks 表直到终态再退出。

用法：.venv/Scripts/python.exe scripts/diag_llm_spotcheck.py "指令1" ["指令2" ...]
注：普通自然语言走 process() 无工具闲聊路径；要触发 sc_* 工具规划，
指令需带 /research 或 /code 前缀（生产入口的唯二工具路径）。
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import load_env_file  # noqa: E402

load_env_file()

from sqlalchemy.orm import sessionmaker  # noqa: E402

from gateway.app import run_im_pipeline  # noqa: E402
from gateway.runtime import build_runtime  # noqa: E402
from persistence.engine import get_engine  # noqa: E402
from persistence.repositories.task_repo import _TERMINAL_STATUSES, TaskRepo  # noqa: E402

CHAT_ID = "oc_a2a2ee45352c449e617b370b970ab686"
APP_ID = os.environ.get("FEISHU_APP_ID", "cli_aa1a8a41f378dcbc")
POLL_TIMEOUT_SEC = 1800  # 单任务最长等待（sc 节点真跑数百秒级）


def make_payload(text: str) -> tuple[dict, str]:
    """构造全新 message_id 的 im.message.receive_v1 假事件 payload。"""
    message_id = f"om_spotcheck_{uuid.uuid4().hex[:16]}"
    payload = {
        "header": {
            "event_id": f"fake_spotcheck_{uuid.uuid4().hex[:12]}",
            "event_type": "im.message.receive_v1",
            "app_id": APP_ID,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_test_spotcheck"},
                "sender_type": "user",
            },
            "message": {
                "chat_id": CHAT_ID,
                "message_id": message_id,  # 全新，绕幂等；同时作轮询锚点
                "message_type": "text",
                "chat_type": "p2p",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": None,
            },
        },
    }
    return payload, message_id


def poll_task(message_id: str, timeout_sec: int = POLL_TIMEOUT_SEC) -> dict:
    """按 message_id 轮询 tasks 表直到终态，返回任务快照 dict。"""
    session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False)()
    repo = TaskRepo(session)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        session.expire_all()  # 研究线程在别的 session 提交，需清缓存读新值
        row = repo.get_by_message_id(message_id)
        if row is not None and row.status in _TERMINAL_STATUSES:
            plan = row.plan_json or {}
            nodes = plan.get("nodes", [])
            return {
                "task_id": row.task_id, "status": row.status,
                "intent": row.intent,
                "tools": [n.get("tool_name") for n in nodes
                          if n.get("kind") == "tool"],
                "error_code": row.error_code,
                "error_message": (row.error_message or "")[:300],
                "reply_excerpt": (row.reply_text or "")[:500],
            }
        time.sleep(5)
    return {"status": "poll_timeout", "message_id": message_id}


def main() -> None:
    """逐条推送抽查指令：推入管线 → 轮询终态 → 打印工具与结果摘要。"""
    ap = argparse.ArgumentParser(description="真 LLM 规划抽查")
    ap.add_argument("texts", nargs="+", help="要抽查的用户指令文本")
    args = ap.parse_args()

    rt = build_runtime()
    for i, text in enumerate(args.texts, 1):
        print(f"\n===== [{i}/{len(args.texts)}] {text} =====", flush=True)
        payload, message_id = make_payload(text)
        try:
            result = run_im_pipeline(rt.app, APP_ID, payload)
        except Exception as exc:  # 抽查脚本：任何异常都要可见
            print(f"PIPELINE_EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
            continue
        print(f"pipeline: {json.dumps(result, ensure_ascii=False, default=str)[:300]}",
              flush=True)
        if result.get("status") != "research_accepted":
            continue  # 非异步研究路径（如闲聊/命令），结果已在 pipeline 返回里
        final = poll_task(message_id)
        print(f"final: {json.dumps(final, ensure_ascii=False, default=str)}",
              flush=True)


if __name__ == "__main__":
    main()
