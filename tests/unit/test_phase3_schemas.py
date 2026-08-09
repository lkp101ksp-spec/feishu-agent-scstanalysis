from shared.errors import DynamicAppendError, FreezeRequired, LoopMaxIterError
from shared.schemas import ChatMessage, SummaryBlock


def test_freeze_required_inherits_feishu():
    e = FreezeRequired("summary too large")
    assert e.code == "FREEZE_REQUIRED"


def test_loop_max_iter_error():
    e = LoopMaxIterError("loop_n5 reached max")
    assert e.code == "LOOP_MAX_ITER"


def test_dynamic_append_error():
    e = DynamicAppendError("invalid dynamic nodes")
    assert e.code == "DYNAMIC_APPEND_FAILED"


def test_summary_block_minimal():
    sb = SummaryBlock(summary_id="s_1", text="...")
    assert sb.kind == "summary"
    assert sb.ref == ""   # 默认空字符串
    assert sb.text == "..."


def test_chat_message_estimated_tokens_default():
    m = ChatMessage(role="user", content="hi")
    assert m.estimated_tokens == 0