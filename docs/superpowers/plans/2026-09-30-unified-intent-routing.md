# 统一入口路由实施计划（静默路由 · 共享工作区 · 失败回退环）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户端无需区分 /research 与 /code——模型静默路由；research 失败自动 code 回退补救并重试 ≤1 次；code 平面与 research 平面共享 bio_workspace 数据视野。

**Architecture:** 复用意图闸既有"卡片批准后 incoming_kwargs 再分发"机制，新增 confirm=false 静默分支直接分发；CodingRunner 工作区根迁至 bio_workspace/_code/，文件读原语经 resolve_read 放开 bio_workspace 只读；research 终态 hook 构造失败回退包交 CodingRunner.run_sync，按 [RETRYABLE] 标记重跑。

**Tech Stack:** Python 3.11 / pytest / 飞书 IM；spec: docs/superpowers/specs/2026-09-30-unified-intent-routing-design.md

**门禁纪律：** 每个 Task 收尾跑 `pytest` 相关文件；全部完成后跑 `./scripts/check.ps1`（BOM + ruff + mypy + pytest 全套）再 commit。注释用中文函数级注释（项目惯例）。代码不写行内注释除非必要。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `config/settings.py` | 新增 `intent_gate_confirm` 设置 + env 解析 | 修改 |
| `orchestrator/intent_gate.py` | IntentGateService 增 `confirm` 参数与静默分发分支 | 修改 |
| `gateway/runtime.py` | 装配 IntentGateService 时透传 confirm | 修改 |
| `orchestrator/app.py` | intent_auto 分支分发到对应 runner | 修改 |
| `orchestrator/coding/workspace.py` | WorkspaceManager 增 bio_root + resolve_read | 修改 |
| `orchestrator/coding/code_tools.py` | 三个读原语切换 resolve_read | 修改 |
| `orchestrator/coding/coding_runner.py` | 工作区根迁移 + 数据集清单注入 | 修改 |
| `orchestrator/research_runner.py` | _execute 返回 failures；_run 失败回退 hook | 修改 |
| `tests/unit/test_intent_gate.py` | 静默模式测试 | 修改 |
| `tests/unit/test_coding_workspace.py` | resolve_read 测试 | 修改 |
| `tests/unit/test_bio_workspace_gc.py` | _code 目录 GC 豁免回归 | 修改 |
| `tests/unit/test_coding_runner.py` | 根迁移 + 清单注入测试 | 修改 |
| `tests/unit/test_research_fallback.py` | 回退环测试（新） | 新建 |

---

## P1 · Task 1: 静默路由（settings + intent_gate + runtime + app）

**Files:**
- Modify: `config/settings.py`（L163-165 附近字段区 + L326-328 env 解析区）
- Modify: `orchestrator/intent_gate.py:120-170`
- Modify: `gateway/runtime.py:372-375`
- Modify: `orchestrator/app.py:350-356`
- Test: `tests/unit/test_intent_gate.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_intent_gate.py` 顶部 import 区追加（若未有）：

```python
from unittest.mock import MagicMock
```

文件末尾追加：

```python
class TestSilentMode:
    """confirm=False 静默路由：不发卡、直接返回 intent_auto + incoming_kwargs。"""

    def _mk(self, route: str):
        llm = MagicMock()
        llm.chat.return_value = f'{{"route": "{route}"}}'
        im = MagicMock()
        svc = IntentGateService(llm=llm, im=im, confirm=False)
        return svc, im

    def _msg(self, text: str = "提取导管细胞重聚类"):
        return IncomingMessage(
            message_id="m1", chat_id="c1", sender_open_id="u1",
            chat_type="p2p", text=text)

    def test_silent_research_returns_intent_auto(self):
        svc, im = self._mk("research")
        out = svc.maybe_offer(self._msg())
        assert out is not None and out["status"] == "intent_auto"
        assert out["route"] == "research"
        assert out["incoming_kwargs"]["text"].startswith("/research ")
        assert out["incoming_kwargs"]["chat_id"] == "c1"
        im.send_card.assert_not_called()
        im.reply.assert_called_once()
        assert svc._pending == {}

    def test_silent_code_route(self):
        svc, im = self._mk("code")
        out = svc.maybe_offer(self._msg("帮我写个脚本"))
        assert out["route"] == "code"
        assert out["incoming_kwargs"]["text"].startswith("/code ")

    def test_silent_chat_falls_through(self):
        svc, im = self._mk("chat")
        assert svc.maybe_offer(self._msg("你好")) is None
        im.reply.assert_not_called()

    def test_silent_hint_reply_failure_still_routes(self):
        svc, im = self._mk("research")
        im.reply.side_effect = RuntimeError("net")
        out = svc.maybe_offer(self._msg())
        assert out["status"] == "intent_auto"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_intent_gate.py::TestSilentMode -v`
Expected: FAIL（`IntentGateService.__init__() got an unexpected keyword argument 'confirm'`）

- [ ] **Step 3: 实现 settings + intent_gate + runtime**

`config/settings.py`：在 `intent_gate_enabled: bool = True`（L163）下一行加：

```python
    intent_gate_confirm: bool = False
```

env 解析区（L326-328 `intent_gate_enabled=` 附近）加：

```python
        intent_gate_confirm=os.environ.get(
            "INTENT_GATE_CONFIRM", "false").lower() == "true",
```

（风格对齐相邻行的 `intent_gate_enabled` 解析。）

`orchestrator/intent_gate.py`：
- 模块 docstring 第 5-6 行"疑似研究意图发确认卡（确认闸防误判白跑 Docker 任务）→ 用户点「确认执行」才转 ResearchRunner"后补一句：
  `2026-09-30 起支持 confirm=False 静默模式：分类命中直接返回 intent_auto + incoming_kwargs，由 app 层即时分发，不再发卡等待（spec 2026-09-30-unified-intent-routing-design）。`
- `__init__` 签名（L120-121）改为：

```python
    def __init__(self, *, llm: LLMRouter, im: IMAdapter, ttl_sec: int = 1800,
                 confirm: bool = True,
                 now: Callable[[], float] = lambda: time.time()) -> None:
        self.llm = llm
        self.im = im
        self.ttl_sec = ttl_sec
        self.confirm = confirm
```

- `maybe_offer` 中 `if route not in _ROUTES: return None`（L145-146）之后、构造 intent_id 之前，插入静默分支：

```python
        if not self.confirm:
            kwargs = {
                "message_id": incoming.message_id,
                "chat_id": incoming.chat_id,
                "sender_open_id": incoming.sender_open_id,
                "chat_type": getattr(incoming, "chat_type", "") or "p2p",
                "text": f"{_ROUTE_LABEL[route]} " + text,
            }
            try:
                preview = text[:80] + ("…" if len(text) > 80 else "")
                self.im.reply(incoming.chat_id,
                              f"[路由] 已按{_ROUTE_TITLE[route]}受理：{preview}")
            except Exception:  # noqa: BLE001 —— 提示失败不阻断路由
                logger.warning("intent auto route hint send failed",
                               exc_info=True)
            return {"status": "intent_auto", "route": route,
                    "incoming_kwargs": kwargs}
```

- `maybe_offer` docstring 首行补："confirm=False 时改走静默分发（返回 intent_auto）。"

`gateway/runtime.py` L372-375 装配处改为：

```python
        orch.intent_gate = IntentGateService(
            llm=llm, im=orch.im,
            ttl_sec=getattr(settings, "intent_gate_ttl_sec", 1800),
            confirm=getattr(settings, "intent_gate_confirm", False),
        )
```

- [ ] **Step 4: app.py 增 intent_auto 分发**

`orchestrator/app.py` L352-356 改为：

```python
        gate = getattr(self, "intent_gate", None)
        if gate is not None:
            offered: Optional[dict[str, Any]] = gate.maybe_offer(incoming)
            if offered is not None and offered.get("status") == "intent_auto":
                # 静默路由（Phase 78）：分类命中即分发，机制同卡片批准回调
                route = offered.get("route", "research")
                runner = getattr(
                    self,
                    "research_runner" if route == "research" else "coding_runner",
                    None)
                if runner is not None:
                    auto_incoming = IncomingMessage(
                        **offered["incoming_kwargs"])
                    runner.handle(auto_incoming)
                    return {"status": "intent_auto_dispatched",
                            "route": route}
                # runner 未装配：回落闲聊（不阻断）
            elif offered is not None:
                return offered
```

（确认 `IncomingMessage` 已在 app.py 顶部 import——L372 gateway/app.py 是 `from shared.schemas import IncomingMessage` 局部导入；orchestrator/app.py 顶部已有 IncomingMessage 则直接用，否则照 gateway 惯例局部 import。）

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `pytest tests/unit/test_intent_gate.py tests/integration/test_intent_gate_callback.py tests/integration/test_message_flow.py -v`
Expected: 全 PASS（旧测试构造时未传 confirm → 默认 True 走卡片老路径）

- [ ] **Step 6: Commit**

```bash
git add config/settings.py orchestrator/intent_gate.py gateway/runtime.py orchestrator/app.py tests/unit/test_intent_gate.py
git commit -m "feat(intent): 静默路由模式——confirm=false 分类命中直接分发，不发确认卡"
```

---

## P1 · Task 2: 共享工作区（workspace.resolve_read + code_tools + runner 根迁移）

**Files:**
- Modify: `orchestrator/coding/workspace.py:56-81`
- Modify: `orchestrator/coding/code_tools.py:130-185`
- Modify: `orchestrator/coding/coding_runner.py:353-366`
- Test: `tests/unit/test_coding_workspace.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_coding_workspace.py` 末尾追加：

```python
class TestResolveRead:
    """只读路径：会话目录内照常；越界但落在 bio_root 内放行；bio_root 外仍拒。"""

    def _mk(self, tmp_path):
        bio = tmp_path / "bio_workspace"
        (bio / "aaaaaaaaaaaa").mkdir(parents=True)
        (bio / "aaaaaaaaaaaa" / "report.txt").write_text("qc", encoding="utf-8")
        ws = WorkspaceManager(bio / "_code", bio_root=bio)
        ws.session_dir("s1")
        return ws, bio

    def test_session_relative_still_works(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        ws.session_dir("s1").joinpath("a.txt").write_text("x", encoding="utf-8")
        assert ws.resolve_read("s1", "a.txt").name == "a.txt"

    def test_bio_root_absolute_readable(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        target = str(bio / "aaaaaaaaaaaa" / "report.txt")
        assert ws.resolve_read("s1", target).name == "report.txt"

    def test_bio_root_relative_dotdot_readable(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        p = ws.resolve_read("s1", "../../aaaaaaaaaaaa/report.txt")
        assert p.name == "report.txt"

    def test_outside_bio_root_blocked(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        with pytest.raises(PathEscapeError):
            ws.resolve_read("s1", str(tmp_path / "elsewhere" / "x.txt"))

    def test_no_bio_root_falls_back_strict(self, tmp_path):
        ws = WorkspaceManager(tmp_path / "ws")
        ws.session_dir("s1")
        with pytest.raises(PathEscapeError):
            ws.resolve_read("s1", "../other/x.txt")
```

（确认文件顶部已 import pytest / WorkspaceManager / PathEscapeError，缺则补。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_coding_workspace.py::TestResolveRead -v`
Expected: FAIL（`WorkspaceManager.__init__() got an unexpected keyword argument 'bio_root'`）

- [ ] **Step 3: 实现 workspace.resolve_read**

`orchestrator/coding/workspace.py`：

```python
    def __init__(self, root: Path, bio_root: Path | None = None) -> None:
        self.root = Path(root)
        self.bio_root = Path(bio_root).resolve() if bio_root else None
```

类末尾追加：

```python
    def resolve_read(self, session_id: str, rel: str) -> Path:
        """只读路径解析：会话目录内照常；越界但落在 bio_root 内放行。

        仅供读原语（read_file/list_dir/search_files）使用——写原语仍走
        resolve_safe。bio_root 为 None 时行为与 resolve_safe 完全一致。
        """
        try:
            return self.resolve_safe(session_id, rel)
        except PathEscapeError:
            if self.bio_root is None:
                raise
        p = Path(rel)
        if not p.is_absolute():
            p = self.session_dir(session_id) / rel
        p = p.resolve()
        if p.is_relative_to(self.bio_root):
            return p
        logger.warning("read path blocked: session=%s rel=%r", session_id, rel)
        raise PathEscapeError(f"path escapes readable area: {rel!r}")
```

- [ ] **Step 4: code_tools 读原语切换**

`orchestrator/coding/code_tools.py` 三处：
- L132（`_op_read_file`）、L162（`_op_list_dir`）、L176（`_op_search_files`）的
  `self.ws.resolve_safe(self.session_id, path)` 改为
  `self.ws.resolve_read(self.session_id, path)`。
- `_op_write_file`（L141）、`_op_edit_file`（L148）**保持不变**（写限定会话目录）。
- 文件头 docstring"文件五原语全部经 WorkspaceManager.resolve_safe 限定在会话目录内"改为：
  `写原语经 resolve_safe 限定会话目录；读原语经 resolve_read 放开 bio_root 只读（Phase 78 共享工作区）。`

- [ ] **Step 5: CodingRunner 根迁移**

`orchestrator/coding/coding_runner.py` L354 改为：

```python
        bio_root_str = str(getattr(s, "bio_workspace_root", "") or "").strip()
        if bio_root_str:
            # Phase 78：会话工作区迁入 bio_workspace/_code/，与数据同树
            bio_root = Path(bio_root_str)
            self.ws = WorkspaceManager(bio_root / "_code", bio_root=bio_root)
        else:
            self.ws = WorkspaceManager(
                Path(getattr(s, "code_workspace_root", "./code_workspace")))
        self.bio_data_roots = getattr(s, "bio_data_roots", "")
```

（L366 已有 `self.bio_workspace_root = getattr(s, "bio_workspace_root", "")`，保留。）

- [ ] **Step 6: 跑测试确认通过 + 回归**

Run: `pytest tests/unit/test_coding_workspace.py tests/unit/test_coding_runner.py tests/unit/test_code_tools.py -v`
Expected: 全 PASS（test_code_tools 若不存在则去掉该文件参数）

- [ ] **Step 7: Commit**

```bash
git add orchestrator/coding/workspace.py orchestrator/coding/code_tools.py orchestrator/coding/coding_runner.py tests/unit/test_coding_workspace.py
git commit -m "feat(workspace): code 会话目录迁入 bio_workspace/_code，读原语放开数据区只读"
```

---

## P1 · Task 3: 数据集清单注入 code 平面

**Files:**
- Modify: `orchestrator/coding/coding_runner.py:421-429`（画像注入块之后）
- Test: `tests/unit/test_coding_runner.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_coding_runner.py` 末尾追加：

```python
class TestWorkspaceContextInject:
    """Phase 78：code 平面系统上下文注入 bio_workspace 数据集清单。"""

    def test_workspace_context_injected(self, tmp_path, monkeypatch):
        import orchestrator.coding.coding_runner as cr_mod
        bio = tmp_path / "bio_workspace"
        ds = bio / "aaaaaaaaaaaa"
        ds.mkdir(parents=True)
        (ds / "processed.h5ad").write_bytes(b"x")  # 元数据读取失败也列名
        captured = {}

        class _FakeLoop:
            def __init__(self, *a, **kw):
                pass

            def run(self, system, task_text):
                captured["system"] = system
                from orchestrator.coding.agent_loop import LoopResult
                return LoopResult(status="final", steps=1,
                                  final_text="[RETRYABLE]", tools_called=[])

        monkeypatch.setattr(cr_mod, "AgentLoop", _FakeLoop)
        runner = _make_runner(tmp_path, bio)  # 复用本文件既有 runner 工厂/fixture
        incoming = _make_incoming()           # 复用本文件既有 incoming 工厂
        runner.run_sync(incoming, "看看有哪些数据")
        assert "aaaaaaaaaaaa" in captured["system"]
```

实施时先读 test_coding_runner.py 既有 fixture 名（runner 工厂 / incoming 工厂 / LoopResult 真实签名），按现状对齐——若构造 FakeLoop 成本过高，改为直接对 `build_workspace_context` 调用点打 monkeypatch 断言被调用且结果拼入 system。**原则：断言 system 文本含 dataset id 或断言注入函数被调，二选一，取改造成本低的。**

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_coding_runner.py::TestWorkspaceContextInject -v`
Expected: FAIL（system 中无清单 / 注入未被调）

- [ ] **Step 3: 实现注入**

`orchestrator/coding/coding_runner.py` L423-429 画像注入块之后追加：

```python
        # Phase 78：workspace 数据集清单注入（对齐 research_runner；
        # /code 也有 sc_* 白名单，无清单会隔空猜 dataset_ref 猜到已 GC 的旧 id）
        try:
            from orchestrator.tools.bio.dataset_profile import (
                build_workspace_context)
            ws_ctx = build_workspace_context(
                self.bio_workspace_root, self.bio_data_roots)
            if ws_ctx:
                system += "\n\n" + ws_ctx
        except Exception:  # noqa: BLE001 —— 清单是可选项，注入失败不阻断任务
            logger.warning("workspace context inject failed", exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_coding_runner.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/coding/coding_runner.py tests/unit/test_coding_runner.py
git commit -m "feat(coding): 系统上下文注入 bio_workspace 数据集清单，与 research 平面同视野"
```

---

## P1 · Task 4: GC 豁免回归测试 + 全量门禁

**Files:**
- Test: `tests/unit/test_bio_workspace_gc.py`

- [ ] **Step 1: 写回归测试**

`tests/unit/test_bio_workspace_gc.py` 末尾追加：

```python
class TestCodeSessionImmune:
    """Phase 78 回归锁：_code 会话目录永不被 GC（正则 ^[0-9a-f]{12} 天然豁免）。"""

    def test_underscore_dirs_untouched(self, tmp_path):
        from orchestrator.bio_workspace_gc import sweep_once
        code_dir = tmp_path / "_code" / "code_s1"
        code_dir.mkdir(parents=True)
        (code_dir / "a.txt").write_text("x", encoding="utf-8")
        out = sweep_once(str(tmp_path), ttl_sec=0, cap_bytes=1,
                         grace_sec=0, now=10**12)
        assert code_dir.is_dir()
        assert "_code" not in out["ttl_deleted"] + out["lru_deleted"]
```

（sweep_once 的真实函数名/签名以 bio_workspace_gc.py 现状为准对齐——先读文件头确认。）

- [ ] **Step 2: 跑测试 + 全量门禁**

Run: `pytest tests/unit/test_bio_workspace_gc.py -v`，然后 `./scripts/check.ps1`
Expected: 全 PASS，门禁绿

- [ ] **Step 3: Commit + push**

```bash
git add tests/unit/test_bio_workspace_gc.py
git commit -m "test(gc): _code 会话目录 GC 豁免回归锁"
git push
```

---

## P1 · Task 5: 真机验收

- [ ] **Step 1: 重启 ws_client**（kill `.ws_client.pid` 的 pid，guardian 自动拉起，看 logs/ws_client_stderr.log 出现 connected）

- [ ] **Step 2: 验收三条**
1. 自然语言发「提取导管细胞重聚类」→ 无确认卡、收到"[路由] 已按研究任务受理"、任务正常执行；
2. /code 任务里 `list_dir "../.."` 或直接读数据文件 → 能看到 bio_workspace 内容；
3. `.env` 无需改动（INTENT_GATE_CONFIRM 缺省 false 即静默）。

- [ ] **Step 3: 回填测试总结**（`测试总结+2026-09-09T01-55-00.md` 追加 P1 验收段）

---

## P2 · Task 6: 失败回退环（research_runner hook）

**Files:**
- Modify: `orchestrator/research_runner.py:283-307`（_run）+ `632-640`（_execute 返回）
- Test: `tests/unit/test_research_fallback.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_research_fallback.py`：

```python
"""Phase 78 P2：research 失败终态自动 code 回退环单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.research_runner import ResearchRunner


def _mk_runner(status: str, final_text: str = ""):
    orch = SimpleNamespace(settings=SimpleNamespace(report_folder_token=""))
    coding = MagicMock()
    coding.run_sync.return_value = {"status": "final", "steps": 2,
                                    "final_text": final_text}
    orch.coding_runner = coding
    im = MagicMock()
    runner = ResearchRunner.__new__(ResearchRunner)
    runner.orch = orch
    runner.im = im
    return runner, coding, im


class TestMaybeCodeFallback:
    def test_success_no_fallback(self):
        runner, coding, _ = _mk_runner("success")
        runner._maybe_code_fallback(MagicMock(), "任务", {"status": "success"})
        coding.run_sync.assert_not_called()

    def test_failed_triggers_fallback_and_retry_on_marker(self):
        runner, coding, im = _mk_runner("failed", final_text="修好了\n[RETRYABLE]")
        runner._run = MagicMock()
        incoming = MagicMock()
        out = {"status": "failed", "failures": ["- n2 SCRIPT_ERROR: boom"]}
        runner._maybe_code_fallback(incoming, "分析X", out)
        brief = coding.run_sync.call_args[0][1]
        assert "分析X" in brief and "SCRIPT_ERROR" in brief
        runner._run.assert_called_once()
        assert runner._run.call_args[1].get("_retried") is True or \
            runner._run.call_args[0][3] is True

    def test_not_retryable_no_rerun(self):
        runner, coding, _ = _mk_runner("failed", final_text="数据缺失 [NOT_RETRYABLE]")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "failed", "failures": []})
        coding.run_sync.assert_called_once()
        runner._run.assert_not_called()

    def test_coding_crash_swallowed(self):
        runner, coding, im = _mk_runner("failed")
        coding.run_sync.side_effect = RuntimeError("boom")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "failed", "failures": []})
        runner._run.assert_not_called()

    def test_partial_failure_triggers(self):
        runner, coding, _ = _mk_runner("success_with_partial_failure",
                                       final_text="[NOT_RETRYABLE]")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "success_with_partial_failure",
                                     "failures": ["- n1 X: y"]})
        coding.run_sync.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_research_fallback.py -v`
Expected: FAIL（`_maybe_code_fallback` 不存在）

- [ ] **Step 3: 实现**

`orchestrator/research_runner.py`：

① `_run` 签名与 hook（L283-291 区）：

```python
    def _run(self, incoming: IncomingMessage, task_text: str,
             card: "_ResearchProgressCard | None" = None,
             _retried: bool = False) -> None:
        """后台线程主体：独立 session 落库，plan→schedule→render→写文档→回复。"""
        card = card or _ResearchProgressCard(self.im, incoming.chat_id, task_text)
        session = self.session_factory()
        try:
            out = self._execute(incoming, task_text, session, card)
            session.commit()
            logger.info("research task done: %s", out.get("status"))
            if not _retried:
                self._maybe_code_fallback(incoming, task_text, out)
        except Exception:
            ...（原样保留）
```

② `_execute` 返回 dict（L632-640）加 failures（`_failure_digest` 已在 L583 附近算过 reply_lines，复用同一调用结果）：

```python
        return {
            "status": result.status,
            "task_id": task_id,
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "node_states": {k: v.value for k, v in result.node_states.items()},
            "doc_written": doc_written,
            "images_sent": images_sent,
            "failures": self._failure_digest(scheduler),
        }
```

（实施时确认 `_failure_digest` 与 `scheduler` 在该作用域可用——L588 附近 reply_lines 已用过，变量名对齐现场。）

③ 类内新增方法（放 `_failure_digest` 附近）：

```python
    def _maybe_code_fallback(self, incoming: IncomingMessage,
                             task_text: str, out: dict[str, Any]) -> None:
        """失败终态自动 code 回退：诊断补救 → [RETRYABLE] 则重跑 ≤1 次。

        Phase 78 P2（spec 2026-09-30-unified-intent-routing-design §5）：
        只在 failed / success_with_partial_failure 触发；回退环自身任何
        异常只记日志 + IM 告知，绝不影响已推送的原任务结果。
        """
        status = out.get("status", "")
        if status not in ("failed", "success_with_partial_failure"):
            return
        coding = getattr(getattr(self, "orch", None), "coding_runner", None)
        if coding is None:
            return
        try:
            self.im.reply(
                incoming.chat_id,
                "[自动回退] 研究任务未全部成功，已转代码路径诊断补救…")
        except Exception:  # noqa: BLE001
            logger.warning("fallback hint send failed", exc_info=True)
        failures = "\n".join(out.get("failures") or ["(无失败明细)"])
        brief = (
            "【自动回退】研究任务执行失败，请诊断并补救。\n"
            f"原始任务：{task_text}\n"
            f"终态：{status}\n"
            f"失败节点：\n{failures}\n"
            "规则：bio_workspace 下的数据文件只读，写操作限定在你的会话目录；"
            "禁止删除任何数据文件。补救完成后，若判断原任务重试可以成功，"
            "最后单独一行输出 [RETRYABLE]，否则输出 [NOT_RETRYABLE]。")
        try:
            res = coding.run_sync(incoming, brief)
        except Exception:  # noqa: BLE001
            logger.exception("code fallback crashed")
            try:
                self.im.reply(incoming.chat_id,
                              "[自动回退] 代码路径补救未能完成，详见服务端日志")
            except Exception:  # noqa: BLE001
                pass
            return
        final_text = str(res.get("final_text") or "")
        if "[RETRYABLE]" not in final_text:
            logger.info("code fallback finished: not retryable")
            return
        logger.info("code fallback marked retryable: rerun research once")
        self._run(incoming, task_text, _retried=True)
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `pytest tests/unit/test_research_fallback.py tests/integration/test_research_runner.py -v`
Expected: 全 PASS（_execute 返回多了 failures 键，既有断言不受影响；若有精确 dict 相等断言则同步更新）

- [ ] **Step 5: 全量门禁 + commit + push**

Run: `./scripts/check.ps1`，全绿后：

```bash
git add orchestrator/research_runner.py tests/unit/test_research_fallback.py
git commit -m "feat(research): 失败终态自动 code 回退环——诊断补救 + [RETRYABLE] 重跑 ≤1 次"
git push
```

- [ ] **Step 6: 真机验收 + 测试总结回填**

构造必失败任务（如自然语言「对数据集 ffffffffffff 做整合」——不存在的 ref）→ 观察：失败推送 → 自动回退提示 → code 诊断 → [NOT_RETRYABLE] 不重跑（或补救后重跑）。回填 `测试总结+2026-09-09T01-55-00.md`。

---

## Self-Review 记录

- Spec 覆盖：§3 静默路由 → Task 1；§4 共享工作区 → Task 2/3；GC 豁免 → Task 4；§5 回退环 → Task 6；§6 错误处理 → 各 Task except 分支；§7 测试 → 各 Task Step。
- 名称一致性：`resolve_read` / `_maybe_code_fallback` / `intent_auto` / `_retried` 全计划一致；`_ROUTE_TITLE` 复用 intent_gate 既有常量。
- 已知留白（实施时对齐现场，非设计问题）：settings env 解析精确行式、test_coding_runner 既有 fixture 名、sweep_once 真实签名、_execute 内 scheduler 变量名。
