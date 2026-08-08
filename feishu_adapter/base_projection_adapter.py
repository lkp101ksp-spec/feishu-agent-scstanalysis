"""Base Projection Adapter：把 PostgreSQL 状态异步投影到飞书 Base。

Phase 1 设计：投影失败仅返回 False，不抛异常（不影响主事务）。
调用方负责记 audit_logs(project_base_failed)。
"""
import logging
from typing import Optional

from feishu_adapter.client import LarkCLI, LarkCLIError

logger = logging.getLogger(__name__)


class BaseProjectionAdapter:
    """对飞书 Base（多维表格）的投影写入薄封装。"""

    def __init__(self, app_token: str, cli: LarkCLI | None = None):
        self.app_token = app_token
        self.cli = cli or LarkCLI()

    def project_task(
        self,
        task_id: str,
        status: str,
        reply_text: str,
        error_message: Optional[str],
    ) -> bool:
        """把一条 task 状态投影到 Base 的 tasks_view 表。

        返回 True 表示成功，False 表示失败（不抛异常）。
        """
        try:
            self.cli.run([
                "base", "record", "create",
                "--app-token", self.app_token,
                "--table", "tasks_view",
                "--fields", f"task_id={task_id}",
                "--fields", f"status={status}",
                "--fields", f"reply_text={(reply_text or '')[:200]}",
                "--fields", f"error_message={error_message or ''}",
            ])
            return True
        except LarkCLIError as e:
            logger.warning("project_task failed task=%s err=%s", task_id, e)
            return False