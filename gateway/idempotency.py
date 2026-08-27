"""网关层幂等键工具：构造幂等键与判定重复。"""
from persistence.repositories.idempotency_repo import IdempotencyRepo


def build_idempotency_key(app_id: str, chat_id: str, message_id: str) -> str:
    """构造幂等键：app_id:chat_id:message_id。"""
    return f"{app_id}:{chat_id}:{message_id}"


def try_reserve(repo: IdempotencyRepo, key: str, task_id: str | None = None) -> bool:
    """尝试保留幂等键。

    返回 True 表示首次处理（应进入业务）；
    返回 False 表示重投（应跳过业务，直接返回 200）。
    """
    return repo.try_reserve(key, task_id=task_id)


def link_task(repo: IdempotencyRepo, key: str, task_id: str) -> None:
    """关联 task_id 到已保留的幂等键。"""
    repo.link_task(key, task_id)
