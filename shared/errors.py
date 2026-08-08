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