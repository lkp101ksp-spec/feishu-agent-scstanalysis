"""自定义异常体系。"""


class FeishuAgentError(Exception):
    """基础异常。"""


class SignatureInvalidError(FeishuAgentError):
    """飞书事件签名校验失败。"""


class RateLimitExceededError(FeishuAgentError):
    """超过限流阈值。"""


class DuplicateMessageError(FeishuAgentError):
    """webhook 重投，已处理过同一条消息。"""


class LLMCallError(FeishuAgentError):
    """LLM 调用失败。"""


class FeishuAdapterError(FeishuAgentError):
    """飞书适配层错误。"""


class DocWriteError(FeishuAgentError):
    """文档写入失败。"""


class BindDocInvalidError(FeishuAgentError):
    """bind-doc 指令格式或参数无效。"""


# === Phase 2 errors ===

class ToolBlockedError(FeishuAgentError):
    """AST P0 命中，工具被拒绝。"""
    code = "TOOL_BLOCKED"


class ToolDeniedError(FeishuAgentError):
    """用户拒绝卡片审批。"""
    code = "TOOL_DENIED"


class ToolNotFoundError(FeishuAgentError):
    """工具未注册。"""
    code = "TOOL_NOT_FOUND"


class SandboxUnavailableError(FeishuAgentError):
    """Docker daemon 不可用或沙箱启动失败。"""
    code = "SANDBOX_UNAVAILABLE"


class FileTooLargeError(FeishuAgentError):
    """文件大小超过 500MB。"""
    code = "FILE_TOO_LARGE"


class RenderError(FeishuAgentError):
    """模板渲染失败。"""
    code = "RENDER_ERROR"


class DAGValidationError(FeishuAgentError):
    """DAG 校验失败（循环依赖/节点引用不存在）。"""
    code = "DAG_VALIDATION_FAILED"


class TaskNotFoundError(FeishuAgentError):
    """Task 不存在。"""
    code = "TASK_NOT_FOUND"


class ArtifactNotFoundError(FeishuAgentError):
    """Artifact 不存在。"""
    code = "ARTIFACT_NOT_FOUND"


# === Phase 3 errors ===

class FreezeRequired(FeishuAgentError):
    """上下文超 95%，强制冻结 session。"""
    code = "FREEZE_REQUIRED"


class LoopMaxIterError(FeishuAgentError):
    """循环节点达到 max_iterations 强制退出。"""
    code = "LOOP_MAX_ITER"


class DynamicAppendError(FeishuAgentError):
    """动态追加节点失败（validate_dag 不通过）。"""
    code = "DYNAMIC_APPEND_FAILED"
