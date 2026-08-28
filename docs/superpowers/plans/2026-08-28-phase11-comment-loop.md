# Phase 11 评论闭环补完 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「导师批评论 → agent 实时感知 → owner 应用 → 评论区回执」闭环跑通：CommentClient SDK 化（零凭据）+ 评论事件接收（ws 长连接）+ 轮询兜底接线 + 处理回执写回。

**Architecture:** 评论 API 走 lark-oapi `BaseRequest` 原始请求模式（对齐 doc_adapter 现行惯例，绕开 BaseResponse.success() 语义坑）；事件用 `register_p2_customized_event` 接收 `drive.notice.comment_add_v1`；事件回调与轮询各自用独立 DB Session（对齐 renew scanner 模式）；sync/notify 复用 Phase 8/9 幂等服务，双通道天然去重。

**Tech Stack:** lark-oapi 1.7.3（已装）/ SQLAlchemy / pytest / ruff

**Spec:** `docs/superpowers/specs/2026-08-28-feishu-research-agent-phase11-design.md`

---

## 前置：用户后台配置（与实施并行，一次性）

发给用户（消息含以下内容）：

```
请点击一键配置链接（飞书内打开，点「启用并授权」）：
https://open.feishu.cn/page/launcher?clientID=cli_aa1a8a41f378dcbc&tp=ccm

若链接 404，手动配置：
1. 开发者后台 → 事件与回调 → 事件订阅 → 添加事件：drive.notice.comment_add_v1
2. 权限管理 → 批量导入，scopes.tenant 贴入：
   ["drive:drive:readonly","docs:document.comment:read",
    "docs:document.comment:create","docs:document.comment:write_only",
    "docx:document:readonly"]
3. 版本管理与发布 → 创建版本并发布
```

---

## 背景知识（零上下文工程师必读）

- 项目根：`i:\飞书agent`，venv：`.venv\Scripts\python.exe`，测试：`-m pytest tests -q`，lint：`-m ruff check <paths>`
- 评论官方 API（真实路径，已核对 SDK）：
  - 列表：`GET /open-apis/drive/v1/files/{file_token}/comments?file_type=docx&user_id_type=open_id`，返回 `data.items[]`，每项：`comment_id`/`user_id`/`is_solved`/`reply_list.replies[]`；**评论正文在 `replies[0].content.elements[]`**（`type=="text"` 的 `text_run.text` 拼接），`replies[1:]` 是后续回复
  - 回复评论：`POST /open-apis/drive/v1/files/{file_token}/comments/{comment_id}/replies?file_type=docx&user_id_type=open_id`，body：`{"content": {"elements": [{"type": "text", "text_run": {"text": "..."}}]}}`
- bot 自身 open_id 获取：`GET /open-apis/bot/v4/info`（SDK 强类型：`sdk.bot.v4.bot.get(...)`，`resp.data.bot.open_id`）——用于过滤 bot 自己发的评论事件防死循环
- 事件注册：`EventDispatcherHandler.builder().register_p2_customized_event("drive.notice.comment_add_v1", handler)`，handler 收 `CustomizedEvent`（`.event` 是原始 dict，`.header.event_id` 可用）。事件 payload dict 字段：`notice_type`（add_comment/add_reply）、`file_token`、`file_type`、`comment_id`、`reply_id?`、`operator_id.open_id`
- SDK 请求惯例（照抄 `feishu_adapter/doc_adapter.py:96-111`）：`lark.BaseRequest.builder().http_method(...).uri(...).token_types({lark.AccessTokenType.TENANT}).queries(...)` → `sdk_client.request(...)` → `json.loads(resp.raw.content)` → `code != 0` 抛错 → 取 `data`
- 下游扁平 dict 契约（`CommentSyncService.sync` 消费，勿破坏）：`comment_id/block_id/user_id/user_name/text/resolved/replies[{reply_id,user_id,user_name,text}]`
- 命令均以 `cwd=i:\飞书agent` 执行

---

### Task 1: CommentClient SDK 化（列表适配 + 回复写回）

**Files:**
- Modify: `feishu_adapter/comment_client.py`（整文件重写）
- Test: `tests/unit/test_comment_client.py`（整文件重写）

- [ ] **Step 1: 重写测试（新契约：sdk_client 注入 + 扁平适配 + reply_comment）**

```python
"""CommentClient 单测：SDK 原始请求模式 + 官方结构→扁平 dict 适配（ADR-0032）。"""
from unittest.mock import MagicMock

import pytest

from feishu_adapter.comment_client import CommentClient, _extract_text, _to_flat


def _fake_response(payload: dict) -> MagicMock:
    """构造 sdk_client.request 返回的伪 RawResponse。"""
    import json as _json
    resp = MagicMock()
    resp.raw.content = _json.dumps(payload).encode("utf-8")
    return resp


def _sdk_list_returning(items: list) -> MagicMock:
    sdk = MagicMock()
    sdk.request.return_value = _fake_response({"code": 0, "data": {"items": items}})
    return sdk


class _FakeLimiter:
    def wait(self) -> None:  # 测试不限流
        pass


def _official_item(cid="c1", text="导师：结论要补统计检验", replies=None,
                   solved=False):
    """构造官方 list API 的单条评论 JSON（正文在 replies[0]）。"""
    return {
        "comment_id": cid, "user_id": "ou_teacher", "is_solved": solved,
        "reply_list": {"replies": [
            {"reply_id": f"{cid}_r0", "user_id": "ou_teacher",
             "content": {"elements": [{"type": "text",
                                       "text_run": {"text": text}}]}},
        ] + (replies or [])},
    }


def test_list_comments_adapts_official_structure():
    """官方结构 → 下游扁平 dict：root 正文取 replies[0]，replies[1:] 为回复。"""
    item = _official_item(replies=[
        {"reply_id": "c1_r1", "user_id": "ou_student",
         "content": {"elements": [{"type": "text",
                                   "text_run": {"text": "已补充"}}]}},
    ])
    client = CommentClient(sdk_client=_sdk_list_returning([item]),
                           rate_limiter=_FakeLimiter())
    out = client.list_comments(doc_id="doccnX")
    assert out == [{
        "comment_id": "c1", "user_id": "ou_teacher", "user_name": "",
        "text": "导师：结论要补统计检验", "resolved": False, "block_id": None,
        "replies": [{"reply_id": "c1_r1", "user_id": "ou_student",
                     "user_name": "", "text": "已补充"}],
    }]


def test_list_comments_multi_text_elements_concat():
    """正文多个 text 片段拼接；无回复时 replies=[]。"""
    item = _official_item()
    item["reply_list"]["replies"][0]["content"]["elements"] = [
        {"type": "text", "text_run": {"text": "第一段"}},
        {"type": "person", "user_id": "ou_x"},  # 非文本片段跳过
        {"type": "text", "text_run": {"text": "第二段"}},
    ]
    client = CommentClient(sdk_client=_sdk_list_returning([item]),
                           rate_limiter=_FakeLimiter())
    out = client.list_comments(doc_id="d")
    assert out[0]["text"] == "第一段第二段"
    assert out[0]["replies"] == []


def test_list_comments_api_error_raises():
    """code != 0 抛 FeishuAgentError。"""
    from shared.errors import FeishuAgentError
    sdk = MagicMock()
    sdk.request.return_value = _fake_response({"code": 1064030, "msg": "denied"})
    client = CommentClient(sdk_client=sdk, rate_limiter=_FakeLimiter())
    with pytest.raises(FeishuAgentError, match="1064030"):
        client.list_comments(doc_id="d")


def test_reply_comment_posts_text_element():
    """reply_comment 用纯文本 element POST 到 replies 接口。"""
    sdk = MagicMock()
    sdk.request.return_value = _fake_response(
        {"code": 0, "data": {"reply": {"reply_id": "c1_r9"}}})
    client = CommentClient(sdk_client=sdk, rate_limiter=_FakeLimiter())
    out = client.reply_comment(file_token="doccnX", comment_id="c1",
                               text="已按评论修改")
    assert out == {"reply_id": "c1_r9"}
    req = sdk.request.call_args.args[0]
    assert "comments/c1/replies" in req.uri
    assert req.body == {"content": {"elements": [
        {"type": "text", "text_run": {"text": "已按评论修改"}}]}}


def test_extract_text_none_safe():
    """_extract_text 对缺字段的 reply 安全返回空串。"""
    assert _extract_text(MagicMock(spec=[])) == ""


def test_to_flat_missing_reply_list():
    """无 reply_list 的评论：text 空串不炸。"""
    class _Empty:  # comment_id/is_solved 存在，reply_list 缺省
        comment_id = "c9"
        user_id = "ou_a"
        is_solved = True
    out = _to_flat(_Empty())
    assert out["text"] == "" and out["resolved"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_client.py -q`
Expected: FAIL/ERROR（`_extract_text`/`_to_flat` 未定义、构造签名不匹配）

- [ ] **Step 3: 重写实现**

```python
"""评论 API 客户端（lark-oapi SDK tenant 直连，ADR-0032）。

官方结构（reply_list.replies[].content.elements 富文本）经 _to_flat 适配为
下游 Phase 8 服务消费的扁平 dict；列表为单页拉取（分页见 ADR-0019 推迟项）。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import lark_oapi as lark

from shared.errors import FeishuAgentError

if TYPE_CHECKING:
    from orchestrator.tools.bio.rate_limiter import RateLimiter


def _extract_text(reply: Any) -> str:
    """从 reply.content.elements 拼接纯文本（仅 type=text 片段）。"""
    content = getattr(reply, "content", None)
    elements = getattr(content, "elements", None) or []
    parts = []
    for el in elements:
        if getattr(el, "type", None) == "text":
            run = getattr(el, "text_run", None)
            if run is not None and getattr(run, "text", None):
                parts.append(run.text)
    return "".join(parts)


def _to_flat(fc: Any) -> dict:
    """官方 FileComment（dict/对象皆可）→ 下游扁平 dict；root 正文取 replies[0]。"""
    reply_list = getattr(fc, "reply_list", None)
    replies = list(getattr(reply_list, "replies", None) or []) \
        if reply_list is not None else []
    first = replies[0] if replies else None
    return {
        "comment_id": getattr(fc, "comment_id", None) or "",
        "user_id": getattr(fc, "user_id", None) or "",
        "user_name": "",
        "text": _extract_text(first) if first is not None else "",
        "resolved": bool(getattr(fc, "is_solved", False)),
        "block_id": None,  # 官方 list 接口不返回 block_id
        "replies": [
            {"reply_id": getattr(r, "reply_id", None) or "",
             "user_id": getattr(r, "user_id", None) or "",
             "user_name": "",
             "text": _extract_text(r)}
            for r in replies[1:]
        ],
    }


class CommentClient:
    """SDK 原始请求模式（对齐 doc_adapter._sdk_request 惯例，ADR-0032）。"""

    def __init__(self, *, sdk_client, rate_limiter: "RateLimiter") -> None:
        self.sdk = sdk_client
        self.rate_limiter = rate_limiter

    def _request(self, method, uri: str, *, queries: dict | None = None,
                 body: dict | None = None) -> dict:
        """BaseRequest 直调评论 API（tenant token 由 SDK 托管），返回 data 段。"""
        builder = (lark.BaseRequest.builder()
                   .http_method(method)
                   .uri(uri)
                   .token_types({lark.AccessTokenType.TENANT}))
        if queries:
            builder = builder.queries(queries)
        if body is not None:
            builder = builder.body(body)
        self.rate_limiter.wait()
        resp = self.sdk.request(builder.build())
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise FeishuAgentError(
                f"comment api failed: code={payload.get('code')} "
                f"msg={payload.get('msg')} uri={uri}")
        return payload.get("data", {})

    def list_comments(self, *, doc_id: str) -> list[dict]:
        """列出文档全部评论（单页）；适配为下游扁平 dict。"""
        data = self._request(
            lark.HttpMethod.GET,
            f"/open-apis/drive/v1/files/{doc_id}/comments",
            queries={"file_type": ["docx"], "user_id_type": ["open_id"]},
        )
        return [_to_flat(fc) for fc in data.get("items", []) or []]

    def list_block_comments(self, *, doc_id: str, block_id: str) -> list[dict]:
        return [c for c in self.list_comments(doc_id=doc_id)
                if c.get("block_id") == block_id]

    def reply_comment(self, *, file_token: str, comment_id: str,
                      text: str) -> dict:
        """在指定评论下回复纯文本（回执写回，ADR-0034）。"""
        data = self._request(
            lark.HttpMethod.POST,
            f"/open-apis/drive/v1/files/{file_token}/comments/{comment_id}/replies",
            queries={"file_type": ["docx"], "user_id_type": ["open_id"]},
            body={"content": {"elements": [
                {"type": "text", "text_run": {"text": text}},
            ]}},
        )
        reply = data.get("reply") or {}
        return {"reply_id": reply.get("reply_id", "")}
```

注意：`_to_flat` 用 `getattr` 兼容 dict 传入（官方 JSON 已是 dict 时同样工作？否——dict 没有 `.comment_id` 属性）。**统一约定：`list_comments` 先把 dict 转属性对象**。在 `list_comments` 里加转换：

```python
        items = data.get("items", []) or []
        return [_to_flat(_AttrWrap(fc)) for fc in items]
```

并在文件顶部（`_extract_text` 之前）加：

```python
class _AttrWrap:
    """dict → 属性访问包装（适配层统一按属性读，测试可传 MagicMock/对象）。"""

    def __init__(self, d: dict | None) -> None:
        self._d = d or {}

    def __getattr__(self, name: str):
        v = self._d.get(name)
        if isinstance(v, dict):
            return _AttrWrap(v)
        if isinstance(v, list):
            return [_AttrWrap(x) if isinstance(x, dict) else x for x in v]
        return v
```

（`__getattr__` 只在属性不存在时触发，返回 None 而非 AttributeError，配合 `_to_flat` 的 `getattr(x, ..., None)` 防御。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_client.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add feishu_adapter/comment_client.py tests/unit/test_comment_client.py
git commit -m "feat(comment): CommentClient 迁移 SDK 原始请求模式——零凭据 + 官方结构扁平适配 + reply_comment 写回（ADR-0032）"
```

---

### Task 2: on_template_upsert 返回版本号（回执文案数据源）

**Files:**
- Modify: `orchestrator/templates/version_service.py:28-34`（insert 后 return）
- Test: `tests/unit/test_version_service.py`（追加一个测试）

- [ ] **Step 1: 追加失败测试**

```python
def test_on_template_upsert_returns_version_number():
    """upsert 后返回新版本号（回执文案数据源，Phase 11）。"""
    svc = _make_service()  # 复用本文件既有 fixture/工厂，首个版本应返回 1
    n = svc.on_template_upsert(
        template_id="tpl_r", name="n", description="d", created_by="ou_x")
    assert n == 1
```

（`_make_service` 换成本文件里既有的服务构造方式；若已有更贴近的既有测试函数构造法，直接照抄其构造三行。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_version_service.py -q`
Expected: FAIL（返回 None）

- [ ] **Step 3: 最小实现**

`orchestrator/templates/version_service.py` 的 `on_template_upsert`，把最后的 `insert(...)` 调用改为接收并返回版本号（insert 无返回值则在 insert 后 `return current + 1`）：

```python
        self.version_repo.insert(
            version_id=new_ulid(),
            template_id=template_id,
            version_number=current + 1,
            name=name, description=description,
            blocks_json=blocks_json, steps_json=steps_json,
            created_by=created_by,
        )
        return current + 1
```

同时把签名返回类型 `-> None` 改为 `-> int`。

- [ ] **Step 4: 跑测试确认通过（含既有 version 测试零回归）**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_version_service.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator/templates/version_service.py tests/unit/test_version_service.py
git commit -m "feat(version): on_template_upsert 返回新版本号（评论回执文案数据源）"
```

---

### Task 3: CommentActionService 回执挂钩

**Files:**
- Modify: `orchestrator/templates/comment_action_service.py`
- Test: `tests/unit/test_comment_action_service.py`（追加 2 个测试）

- [ ] **Step 1: 追加失败测试**

```python
def test_apply_sends_receipt_after_applied(_services):
    """applied 的评论逐条回写回执；skipped/failed 不回执。"""
    repo, tpl_repo, vs, comment_repo, pending = _services  # 换成本文件既有构造
    client = MagicMock()
    svc = CommentActionService(comment_repo, tpl_repo, vs,
                               comment_client=client)
    out = svc.apply(doc_id="d1", caller_open_id=pending_owner)  # 既有 owner
    assert out["applied"] >= 1
    # 每条 applied 评论恰好一次 reply_comment，文案含模板 id 与版本号
    for call in client.reply_comment.call_args_list:
        kwargs = call.kwargs
        assert kwargs["file_token"] == "d1"
        assert "已按此评论完成修改" in kwargs["text"]
        assert "版本" in kwargs["text"]


def test_apply_receipt_failure_does_not_break(_services):
    """回执抛错不阻断 apply 主流程，评论仍被 mark_processed。"""
    repo, tpl_repo, vs, comment_repo, pending = _services
    client = MagicMock()
    client.reply_comment.side_effect = RuntimeError("network down")
    svc = CommentActionService(comment_repo, tpl_repo, vs,
                               comment_client=client)
    out = svc.apply(doc_id="d1", caller_open_id=pending_owner)
    assert out["failed"] == 0  # 业务动作成功，仅回执降级为 warning
```

（`_services`/`pending_owner` 换成本文件既有的 fixture/局部构造——本文件已有 apply 成功路径测试，照抄其 arrange 三行。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_action_service.py -q`
Expected: FAIL（`CommentActionService.__init__()` 不接受 `comment_client`）

- [ ] **Step 3: 实现**

`comment_action_service.py` 三处改动：

```python
import logging
logger = logging.getLogger(__name__)
```

`__init__` 加可选参数：

```python
    def __init__(self, comment_repo, template_repo, version_service,
                 comment_client=None) -> None:
        self.comment_repo = comment_repo
        self.template_repo = template_repo
        self.version_service = version_service
        self.comment_client = comment_client  # 可选：回执写回（ADR-0034）
```

`apply` 的 applied 分支扩为（`mark_processed` 后）：

```python
            if detail["status"] == "applied":
                applied += 1
                self.comment_repo.mark_processed(c.comment_id)
                self._send_receipt(doc_id=doc_id, comment=c, detail=detail)
```

`_execute` 的成功 return 前接住版本号：`return {"kind": parsed.kind, "status": "applied", "version_number": ver_no}`（`ver_no = self.version_service.on_template_upsert(...)`）。

新增方法：

```python
    def _send_receipt(self, *, doc_id: str, comment, detail: dict) -> None:
        """对已应用评论回写回执；失败仅记 warning 不阻断（ADR-0034）。"""
        if self.comment_client is None:
            return
        text = (f"已按此评论完成修改：模板 {detail.get('template_id', '')} "
                f"已更新至版本 {detail.get('version_number', '?')}。")
        try:
            self.comment_client.reply_comment(
                file_token=doc_id, comment_id=comment.comment_id, text=text)
        except Exception:
            logger.warning("receipt failed for comment %s",
                           comment.comment_id, exc_info=True)
```

（`detail` 里补 `template_id`：`_execute` 成功 return 的 dict 加 `"template_id": parsed.template_id"`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_action_service.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add orchestrator/templates/comment_action_service.py tests/unit/test_comment_action_service.py
git commit -m "feat(comment): apply 成功后回写评论回执「已按评论修改（版本 N）」，失败降级 warning（ADR-0034）"
```

---

### Task 4: CommentEventService（事件处理：防循环→绑定过滤→sync+notify）

**Files:**
- Create: `feishu_adapter/bot_info.py`（bot open_id 获取）
- Create: `orchestrator/templates/comment_event_service.py`
- Test: `tests/unit/test_comment_event_service.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""CommentEventService 单测：防循环 / 绑定过滤 / sync+notify 链路。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orchestrator.templates.comment_event_service import CommentEventService


def _svc(bot_open_id="ou_bot", bound_doc="doccnX", expired=False):
    session_repo = MagicMock()
    session_repo.list_active.return_value = [
        MagicMock(bound_doc_id=bound_doc, owner_open_id="ou_owner",
                  source_chat_id="oc_1",
                  bind_expires_at=(
                      datetime.now(timezone.utc) - timedelta(seconds=1)
                      if expired else
                      datetime.now(timezone.utc) + timedelta(hours=1)),
                  )
    ]
    sync = MagicMock()
    sync.sync.return_value = {"fetched": 1, "new": 1, "updated": 0}
    notify = MagicMock()
    notify.notify_new_pending.return_value = {"notified": 1}
    svc = CommentEventService(
        session_repo=session_repo, sync_service=sync, notify_service=notify,
        bot_open_id=bot_open_id)
    return svc, sync, notify


def test_handle_syncs_and_notifies_for_bound_doc():
    svc, sync, notify = _svc()
    out = svc.handle(file_token="doccnX", operator_open_id="ou_teacher")
    sync.sync.assert_called_once_with(doc_id="doccnX")
    notify.notify_new_pending.assert_called_once_with(
        doc_id="doccnX", owner_open_id="ou_owner", chat_id="oc_1")
    assert out["status"] == "handled"


def test_handle_ignores_bot_own_comments():
    """bot 自己的评论（回执写回触发）直接忽略，防死循环。"""
    svc, sync, notify = _svc(bot_open_id="ou_bot")
    out = svc.handle(file_token="doccnX", operator_open_id="ou_bot")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_bot_self"


def test_handle_ignores_unbound_doc():
    svc, sync, notify = _svc()
    out = svc.handle(file_token="doccnOTHER", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_unbound"


def test_handle_ignores_expired_binding():
    svc, sync, notify = _svc(expired=True)
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_unbound"


def test_handle_without_bot_open_id_skips_conservatively():
    """拿不到 bot open_id 时保守跳过（宁可漏处理也不冒死循环风险）。"""
    svc, sync, notify = _svc(bot_open_id=None)
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "skipped_no_bot_id"


def test_handle_sync_exception_isolated():
    """单事件异常吃掉，返回 error 不抛出（保长连接）。"""
    svc, sync, notify = _svc()
    sync.sync.side_effect = RuntimeError("boom")
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    assert out["status"] == "error"


def test_get_bot_open_id_caches_and_falls_back():
    """get_bot_open_id 拉取一次并缓存；失败返回 None。"""
    from feishu_adapter.bot_info import get_bot_open_id
    sdk = MagicMock()
    resp = MagicMock()
    resp.success.return_value = True
    resp.data.bot.open_id = "ou_bot_9"
    sdk.bot.v4.bot.get.return_value = resp
    assert get_bot_open_id(sdk) == "ou_bot_9"
    assert get_bot_open_id(sdk) == "ou_bot_9"  # 缓存：只调一次 API
    sdk.bot.v4.bot.get.assert_called_once()
    bad = MagicMock()
    bad.bot.v4.bot.get.side_effect = RuntimeError("net")
    assert get_bot_open_id(bad) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_event_service.py -q`
Expected: ERROR（模块不存在）

- [ ] **Step 3: 实现**

`feishu_adapter/bot_info.py`：

```python
"""bot 自身信息（防评论事件死循环的过滤依据，ADR-0033）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CACHE: dict[str, str | None] = {}


def get_bot_open_id(sdk_client) -> str | None:
    """获取 bot open_id（进程级缓存）；失败返回 None（调用方保守跳过）。"""
    if "v" in _CACHE:
        return _CACHE["v"]
    try:
        from lark_oapi.api.bot.v4 import GetBotRequest
        resp = sdk_client.bot.v4.bot.get(GetBotRequest.builder().build())
        if resp.success() and resp.data and resp.data.bot:
            _CACHE["v"] = resp.data.bot.open_id
            return _CACHE["v"]
    except Exception:
        logger.warning("get bot info failed", exc_info=True)
    _CACHE["v"] = None
    return None
```

（若 `GetBotRequest` 的 import 路径在 1.7.3 下为 `lark_oapi.api.bot.v4.model.get_bot_request import GetBotRequest`，以实际 `python -c "from lark_oapi.api.bot.v4 import GetBotRequest"` 验证为准——跑一次 import 确认后写正确路径。）

`orchestrator/templates/comment_event_service.py`：

```python
"""Phase 11: 评论事件处理服务（防循环 → 绑定过滤 → sync + notify，ADR-0033）。

事件回调运行在 SDK ws 线程：本服务必须绑定独立 DB Session 组装（runtime 负责），
严禁与主管线共享 Session。sync/notify 复用 Phase 8/9 幂等服务，
与轮询通道并发触发同一 doc 不会重复落库或重复推送。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CommentEventService:
    def __init__(self, *, session_repo, sync_service, notify_service,
                 bot_open_id: str | None) -> None:
        self.session_repo = session_repo
        self.sync_service = sync_service
        self.notify_service = notify_service
        self.bot_open_id = bot_open_id

    def handle(self, *, file_token: str, operator_open_id: str) -> dict:
        """处理一条评论事件；异常吃掉返回 error（保长连接）。"""
        try:
            return self._handle(file_token=file_token,
                                operator_open_id=operator_open_id)
        except Exception:
            logger.exception("comment event handling failed: %s", file_token)
            return {"status": "error", "file_token": file_token}

    def _handle(self, *, file_token: str, operator_open_id: str) -> dict:
        # 1. 防循环：bot 自身评论（回执写回触发）直接忽略
        if self.bot_open_id is None:
            return {"status": "skipped_no_bot_id"}
        if operator_open_id == self.bot_open_id:
            return {"status": "ignored_bot_self", "file_token": file_token}

        # 2. 绑定过滤：找该 doc 的活跃绑定 session（未过期）
        now = datetime.now(timezone.utc)
        hit = None
        for s in self.session_repo.list_active():
            if s.bound_doc_id != file_token:
                continue
            exp = s.bind_expires_at
            if exp is None:
                hit = s
                break
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                hit = s
                break
        if hit is None:
            return {"status": "ignored_unbound", "file_token": file_token}

        # 3. 触发同步 + 通知（幂等，与轮询通道共用）
        self.sync_service.sync(doc_id=file_token)
        out = self.notify_service.notify_new_pending(
            doc_id=file_token, owner_open_id=hit.owner_open_id,
            chat_id=hit.source_chat_id)
        return {"status": "handled", "file_token": file_token, **out}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_comment_event_service.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add feishu_adapter/bot_info.py orchestrator/templates/comment_event_service.py tests/unit/test_comment_event_service.py
git commit -m "feat(comment): CommentEventService——事件防循环+绑定过滤+sync/notify（ADR-0033）"
```

---

### Task 5: runtime 装配改造（零凭据 + 独立 session + 双服务接线）

**Files:**
- Modify: `gateway/runtime.py:185-258`（评论段重写 + Runtime 容器扩展）
- Modify: `.env.example`（删 FEISHU_API_* 两行及注释）

- [ ] **Step 1: 改 runtime.py**

（a）`Runtime.__init__` 签名与字段加 `comment_event_service=None, auto_sync_worker=None`：

```python
class Runtime:
    """组装产物容器：app（FastAPI）+ ws 进程所需句柄。"""

    def __init__(self, app, orchestrator: Orchestrator, settings: Settings,
                 renew_scan_service=None, renew_scan_interval_sec: int = 60,
                 comment_event_service=None, auto_sync_worker=None):
        self.app = app
        self.orchestrator = orchestrator
        self.settings = settings
        self.renew_scan_service = renew_scan_service
        self.renew_scan_interval_sec = renew_scan_interval_sec
        # 评论事件处理（独立 session：ws 回调线程不共享主管线 Session）
        self.comment_event_service = comment_event_service
        self.auto_sync_worker = auto_sync_worker
```

（b）模块 docstring 的「可选环境变量」段删除 `FEISHU_API_BASE_URL / FEISHU_API_TOKEN` 行。

（c）评论段（原 185-212 行 `if api_base and api_token:` 块）整体替换为：

```python
    # --- Phase 7-9：评论闭环（SDK 直连零凭据，ADR-0032；无条件组装） ---
    comment_client = CommentClient(
        sdk_client=sdk, rate_limiter=RateLimiter(rate=3.0, per_sec=1.0),
    )
    comment_service = CommentService(comment_client)
    comment_repo = CommentRepo(session)
    comment_sync_service = CommentSyncService(comment_client, comment_repo)
    comment_action_service = CommentActionService(
        comment_repo, template_repo, version_service,
        comment_client=comment_client,
    )
    notify = CommentNotifyService(
        comment_repo, CommentNotifyRepo(session), im,
    )
    orch.comment_service = comment_service
    orch.comment_sync_service = comment_sync_service
    orch.comment_action_service = comment_action_service
```

同时删除 101-102 行的 `api_base/api_token` 读取与 `DocAdapter` 调用中的 `base_url=api_base, api_token=api_token` 实参（`DocAdapter(cli=cli, sdk_client=sdk)`——先 grep 确认 doc_adapter 的 `base_url/api_token` 参数是否有默认值，无则传 `base_url="", api_token=""`）。

（d）`create_app` 调用处：`comment_service` 等现在恒非 None，传参不变。

（e）`return Runtime(...)` 前追加两个独立 session 组装（对齐 renew_scan_service 模式）：

```python
    # --- 评论事件服务（独立 event_session：ws 回调线程隔离，ADR-0033） ---
    from feishu_adapter.bot_info import get_bot_open_id
    event_session = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()
    event_client = CommentClient(
        sdk_client=sdk, rate_limiter=RateLimiter(rate=3.0, per_sec=1.0),
    )
    comment_event_service = CommentEventService(
        session_repo=SessionRepo(event_session),
        sync_service=CommentSyncService(
            event_client, CommentRepo(event_session)),
        notify_service=CommentNotifyService(
            CommentRepo(event_session), CommentNotifyRepo(event_session), im),
        bot_open_id=get_bot_open_id(sdk),
    )

    # --- 轮询兜底 worker（独立 scan_session：ws_client 守护线程隔离） ---
    scan_session2 = sessionmaker(
        bind=get_engine(), expire_on_commit=False, autoflush=False,
    )()
    auto_sync_worker = CommentAutoSyncWorker(
        session_repo=SessionRepo(scan_session2),
        sync_service=CommentSyncService(
            CommentClient(sdk_client=sdk,
                          rate_limiter=RateLimiter(rate=3.0, per_sec=1.0)),
            CommentRepo(scan_session2)),
        notify_service=CommentNotifyService(
            CommentRepo(scan_session2), CommentNotifyRepo(scan_session2), im),
        interval_sec=settings.comment_sync_interval_sec,
    )
```

`return Runtime(...)` 加 `comment_event_service=comment_event_service, auto_sync_worker=auto_sync_worker`。

顶部 import 増加：`from feishu_adapter.comment_client import CommentClient`、`from orchestrator.templates.comment_event_service import CommentEventService`、`from persistence.repositories.comment_repo import CommentRepo`、`from persistence.repositories.comment_notify_repo import CommentNotifyRepo`（`CommentService/CommentSyncService/CommentNotifyService/CommentAutoSyncWorker` 已在既有 import 列表）。

（f）`.env.example` 删除：

```
FEISHU_API_BASE_URL=...
FEISHU_API_TOKEN=...
```

两行及其注释行（若有）。

- [ ] **Step 2: 冒烟验证组装**

Run: `.venv\Scripts\python.exe -c "from gateway.runtime import build_runtime; rt = build_runtime(); print('event_svc:', rt.comment_event_service is not None); print('worker:', rt.auto_sync_worker is not None); print('bot_id:', rt.comment_event_service.bot_open_id)"`
Expected: 三行 True/True/None 或真实 open_id（bot info 需要权限，未配时 None 合法——Task 7 后会变真实值）。若 import 报错按报错修。

- [ ] **Step 3: 跑全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: 与改前同数 passed（runtime 相关既有测试若断言「无凭据时评论服务为 None」需同步改：grep `FEISHU_API` in tests，把条件装配断言改为恒组装断言）

- [ ] **Step 4: Commit**

```bash
git add gateway/runtime.py .env.example tests/
git commit -m "feat(runtime): 评论子系统零凭据无条件组装 + 事件/轮询双服务独立 session 接线（ADR-0032/0033）"
```

---

### Task 6: ws_client 接线（事件注册 + 轮询守护线程）

**Files:**
- Modify: `gateway/ws_client.py`（dispatcher 加注册 + start_auto_sync_scanner + main 接线）
- Test: `tests/unit/test_ws_client.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_comment_event_to_payload_extracts_fields():
    """CustomizedEvent(dict) → 归一化 payload。"""
    ev = MagicMock()
    ev.event = {
        "notice_type": "add_comment", "file_token": "doccnX",
        "file_type": "docx", "comment_id": "c1",
        "operator_id": {"open_id": "ou_teacher"},
    }
    p = comment_event_to_payload(ev)
    assert p == {"notice_type": "add_comment", "file_token": "doccnX",
                 "comment_id": "c1",
                 "operator_open_id": "ou_teacher"}


def test_run_auto_sync_tick_once():
    """tick_once 调 worker.tick 一次并返回结果。"""
    worker = MagicMock()
    worker.tick.return_value = {"synced": 1}
    assert run_auto_sync_tick(worker) == {"synced": 1}
    worker.tick.assert_called_once()


def test_start_auto_sync_scanner_daemon_thread():
    """扫描线程 daemon + 周期调用 tick；worker None 时 noop 返回 None。"""
    import time as _t
    worker = MagicMock()
    calls = []
    worker.tick.side_effect = lambda: (calls.append(1),
                                       _t.sleep(0.05))[0]
    t = start_auto_sync_scanner(worker, interval_sec=0)
    assert t is not None and t.daemon is True
    _t.sleep(0.2)
    worker.tick.assert_called()
    assert start_auto_sync_scanner(None) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client.py -q`
Expected: FAIL（`comment_event_to_payload`/`run_auto_sync_tick`/`start_auto_sync_scanner` 未定义）

- [ ] **Step 3: 实现（ws_client.py 三处）**

顶部 import 加：`from lark_oapi.event.custom import CustomizedEvent`

模块级新函数（放在 `start_renew_scanner` 之后）：

```python
def comment_event_to_payload(ev: CustomizedEvent) -> dict:
    """评论事件原始 dict → 归一化 payload（file_token/operator/comment_id）。"""
    e: dict = dict(ev.event or {})
    operator = e.get("operator_id") or {}
    return {
        "notice_type": e.get("notice_type", ""),
        "file_token": e.get("file_token", ""),
        "comment_id": e.get("comment_id", ""),
        "operator_open_id": operator.get("open_id", ""),
    }


def run_auto_sync_tick(worker) -> dict:
    """单轮评论轮询兜底（线程内无事件循环，同步直调 tick）。"""
    return worker.tick()


def start_auto_sync_scanner(worker, interval_sec: int = 300):
    """启动评论轮询兜底守护线程（ws 模式下 FastAPI startup 钩子不触发）。"""
    if worker is None:
        return None
    interval = interval_sec or 300

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                run_auto_sync_tick(worker)
            except Exception:
                logger.exception("auto comment sync tick failed")

    t = threading.Thread(target=loop, daemon=True, name="comment-auto-sync")
    t.start()
    logger.info("auto comment sync scanner started (interval=%ss)", interval)
    return t
```

dispatcher 的 builder 链（`.register_p2_card_action_trigger(on_card)` 后）追加：

```python
    def on_doc_comment(ev: CustomizedEvent) -> None:
        svc = getattr(rt, "comment_event_service", None)
        if svc is None:
            return
        payload = comment_event_to_payload(ev)
        result = svc.handle(file_token=payload["file_token"],
                            operator_open_id=payload["operator_open_id"])
        logger.info("ws comment event handled: %s", result)

    # builder 链上：
        .register_p2_customized_event("drive.notice.comment_add_v1",
                                      on_doc_comment)
```

`main()` 的 `start_renew_scanner(rt)` 后加：

```python
    start_auto_sync_scanner(rt.auto_sync_worker,
                            interval_sec=rt.settings.comment_sync_interval_sec)
```

- [ ] **Step 4: 跑测试确认通过 + dispatcher 冒烟**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_ws_client.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add gateway/ws_client.py tests/unit/test_ws_client.py
git commit -m "feat(ws): 注册 drive.notice.comment_add_v1 事件 + 评论轮询兜底守护线程（ADR-0033）"
```

---

### Task 7: 诊断脚本（真 API 结构校验，权限配置完成后运行）

**Files:**
- Create: `scripts/diag_comments.py`

- [ ] **Step 1: 写脚本**

```python
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
```

- [ ] **Step 2: 运行校验（需用户已完成前置后台配置）**

Run: `.venv\Scripts\python.exe scripts\diag_comments.py list L9AXd9xmdoZuSgx75mccKB82nkb`
Expected: 若文档已有评论，打印扁平 dict（验证 `_to_flat` 适配真实结构；若字段与预期不符，修 `_to_flat` 后重跑本脚本与 Task 1 测试）。权限未生效时会打印 code=1064030 类错误——提示用户完成后台发布。

- [ ] **Step 3: Commit**

```bash
git add scripts/diag_comments.py
git commit -m "feat(diag): 评论真机诊断脚本（list/reply 结构校验）"
```

---

### Task 8: ADR ×3 + 文档收尾

**Files:**
- Create: `docs/superpowers/specs/adrs/0032-comment-client-sdk-migration.md`
- Create: `docs/superpowers/specs/adrs/0033-comment-event-plus-polling-dual-channel.md`
- Create: `docs/superpowers/specs/adrs/0034-comment-receipt-writeback.md`
- Modify: `docs/联调指南.md`（§5 冒烟清单补评论闭环项 + §6 排障补 2 条）
- Modify: spec §14 实施结果（Phase 11 spec 尾部追加）

- [ ] **Step 1: 写三份 ADR**

各 ADR 按 adrs/ 目录既有格式（背景/决策/备选/负面后果/回滚条件）。要点：

**0032**：决策=CommentClient 由 httpx+静态 token 迁移 lark-oapi SDK BaseRequest 模式（对齐 doc_adapter 惯例）；备选=强类型 file_comment resource（弃：BaseResponse.success() 语义坑 ec55187 前科）+ 保留 httpx（弃：token 手工维护）；负面=SDK 升级可能变内部约定；回滚=评论功能不可用时恢复 httpx 路径（git revert 单 commit）。

**0033**：决策=事件为主（drive.notice.comment_add_v1 via ws 长连接）+ auto_sync_worker 轮询兜底双通道，修订 ADR-0024 纯轮询；依据=官方评论事件仅支持 ws 且项目已具备长连接（ADR-0031）；防循环=operator==bot open_id 过滤（bot info 拉取失败保守跳过）；负面=双通道并发依赖 sync 幂等与 notify 去重表（已具备）；回滚=事件注册移除即回退纯轮询。

**0034**：决策=apply 成功后对已处理评论回写固定文案回执（修订 ADR-0015「不写评论」）；范围=仅被动回执、不 LLM 生成、不主动评论；失败=降级 warning 不阻断；回滚=comment_client=None 注入即禁用。

- [ ] **Step 2: 联调指南更新**

§5 冒烟清单追加：评论事件（真文档发评论 → IM 收通知）/ 回执（/comment-apply → 评论区出现回执）。§6 排障追加：

```
- **评论事件收不到**：确认后台已订阅 drive.notice.comment_add_v1（长连接模式）且已发布版本；bot 需为文档协作者。
- **评论 API 1064030**：权限未生效——批量导入评论 scopes 后必须重新发布版本。
```

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: Phase 11 ADR-0032/0033/0034 + 联调指南评论闭环条目"
```

---

### Task 9: 全量回归 + 重启 + 真机验证

- [ ] **Step 1: 全量门**

Run: `.venv\Scripts\python.exe -m pytest tests -q; .venv\Scripts\python.exe -m ruff check gateway orchestrator feishu_adapter tests scripts`
Expected: 全部 passed（预期 ~580）+ ruff 0 error

- [ ] **Step 2: 重启 ws_client（先清旧进程）**

```powershell
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like '*ws_client*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Run（后台）: `.venv\Scripts\python.exe -m gateway.ws_client`
Expected 日志：`auto comment sync scanner started (interval=300s)` + `connected to wss://...` + bot open_id 相关无报错

- [ ] **Step 3: 用户真机验证清单（发给用户）**

1. 在绑定的 wiki 文档里对任意段落添加一条评论（内容随意）
2. 预期 ≤5s：IM 收到「[评论动作] doc … 有 N 条待处理」（普通评论文案不计动作时不推送——先发一条含 `/revise tpl_xxx 改一下措辞` 的评论确保触发）
3. 发 `/comments-sync <doc_id>` → 回复「[成功] 已同步 …」
4. 发 `/comment-apply <doc_id>` → 回复「[结果] 已应用 …」，且**文档评论区出现 bot 回执「已按此评论完成修改：模板 …」**
5. 观察日志出现 `ws comment event handled: {'status': 'handled', ...}`

- [ ] **Step 4: 更新测试总结文件 + spec §14 实施结果**

`测试总结+2026-08-08T23-20-00.md` 追加 Phase 11 联调记录；spec §14 填交付物/修正/最终测试数。

- [ ] **Step 5: 收尾 Commit**

```bash
git add "测试总结+2026-08-08T23-20-00.md" docs/superpowers/specs/2026-08-28-feishu-research-agent-phase11-design.md
git commit -m "docs: Phase 11 实施结果与测试总结收尾"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3（Task 1/5）、§4（Task 4/5/6）、§5（Task 5/6）、§6（Task 2/3）、§8（前置段）、§9（各 Task 测试 + Task 7/9 真机）、§11（Task 8）——全覆盖
- **类型一致性**：`CommentClient(sdk_client=, rate_limiter=)`、`reply_comment(file_token=, comment_id=, text=)`、`CommentEventService.handle(file_token=, operator_open_id=)`、`on_template_upsert -> int` 在各 Task 间一致
- **已知实施期核对点**（不阻塞，现场验证）：① `GetBotRequest` import 路径；② doc_adapter 的 `base_url/api_token` 参数默认值；③ 既有测试中对 `FEISHU_API_*` 条件装配的断言（grep 改造）
