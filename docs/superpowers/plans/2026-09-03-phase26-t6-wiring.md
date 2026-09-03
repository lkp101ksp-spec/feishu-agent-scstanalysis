# Phase 26 T6：settings 字段 + code_approval 卡片回调 + /code 路由

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 背景：spec §5/§6——`/code` 需要配置七字段；审批卡回调新增 `code_approval` action（审批项为会话级短生命周期**不落库**，owner 校验靠发卡时内嵌 value 比对，不查 doc_writes）；orchestrator 进程入口在 `/research` 分支后加 `/code` 分支。

**Files:**
- Modify: `config/settings.py`（Settings dataclass 七字段 + `load_settings()` env 读取）
- Modify: `gateway/app.py`（`process_card_payload` 加 code_approval 分支）
- Modify: `orchestrator/app.py`（`process()` 加 /code 分支，L199-206 /research 分支之后）
- Test: `tests/integration/test_renew_card_callback.py`（文件末尾追加）
- Test: `tests/integration/test_message_flow.py`（文件末尾追加 /code 路由测试）

- [ ] **Step 1: settings 七字段**

`config/settings.py` Settings dataclass 末尾（`bio_workspace_gc_enabled: bool = True` 之后）追加：

```python
    # === Phase 26: /code agentic coding ===
    # 会话工作区根目录（主机路径；每会话一个子目录，跨任务持久）
    code_workspace_root: str = "./code_workspace"
    # skill 目录（skills/<name>/SKILL.md + tools.yaml）
    code_skills_dir: str = "./skills"
    # AgentLoop 三上限：步数 / token 预算 / 墙钟超时（秒）
    code_max_steps: int = 25
    code_token_budget: int = 200000
    code_timeout_sec: int = 3600
    # /code 专用模型（空 = 用 router 主模型）
    code_model: str = ""
    # registry 工具白名单（逗号分隔前缀通配；AgentLoop 额外可调的既有工具）
    code_registry_tools: str = "sc_*"
```

`load_settings()` 返回的 `Settings(...)` 构造参数末尾（`bio_workspace_gc_enabled=...` 之后）追加：

```python
        code_workspace_root=os.environ.get("CODE_WORKSPACE_ROOT", "./code_workspace"),
        code_skills_dir=os.environ.get("CODE_SKILLS_DIR", "./skills"),
        code_max_steps=int(os.environ.get("CODE_MAX_STEPS", "25")),
        code_token_budget=int(os.environ.get("CODE_TOKEN_BUDGET", "200000")),
        code_timeout_sec=int(os.environ.get("CODE_TIMEOUT_SEC", "3600")),
        code_model=os.environ.get("CODE_MODEL", ""),
        code_registry_tools=os.environ.get("CODE_REGISTRY_TOOLS", "sc_*"),
```

- [ ] **Step 2: gateway code_approval 分支**

`gateway/app.py` `process_card_payload` 内，`if action in ("research_writeback", "node_l2_approval"):` 块结束之后、函数末尾 `return {"ok": True}` 之前插入：

```python
    # Phase 26：code_approval 分支（/code 命令与 skill L2 工具审批）。
    # 审批项为会话级短生命周期、不落库：owner 由发卡时内嵌 value 比对
    if action == "code_approval":
        broker = ctx.approval_broker
        if broker is None:
            logger.warning("code_approval received but broker not configured")
            return {"ok": False, "reason": "approval broker not configured"}
        operator = payload.get("open_id", "")
        owner = payload.get("owner", "")
        if owner and operator and owner != operator:
            logger.warning("code_approval forbidden: operator=%s owner=%s",
                           operator, owner)
            return {"ok": False, "status": "forbidden"}
        decision = payload.get("decision", "")
        decided = broker.decide(
            payload.get("code_approval_id", ""), decision, operator=operator)
        return {"ok": decided,
                "status": "decided" if decided else "already_handled",
                "decision": decision if decided else ""}
```

- [ ] **Step 3: orchestrator /code 路由**

`orchestrator/app.py` `process()` 内，`/research` 分支（L201-206）`return runner.handle(incoming)` 之后、群聊门控（1.7）之前插入：

```python
        # 1.68 /code 指令：agentic coding 后台执行（Phase 26）
        if stripped == "/code" or stripped.startswith("/code "):
            runner = getattr(self, "coding_runner", None)
            if runner is None:
                self.im.reply(incoming.chat_id, "[错误] coding 引擎未配置")
                return {"status": "coding_unavailable"}
            return runner.handle(incoming)
```

- [ ] **Step 4: 卡片回调测试**

`tests/integration/test_renew_card_callback.py` 末尾追加（复用同文件 `client_with_broker` fixture 与 `_post_card` helper；code_approval 不需要 doc_writes 行，但 audit 兜底要求 session_factory 已注入——fixture 已满足）：

```python
# === Phase 26：code_approval 分支 ===


def test_code_approval_decide_reaches_broker(client_with_broker):
    """owner 匹配：决策写入 broker，coding 线程 wait 能取到。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca1",
        "decision": "approve", "open_id": "ou_1", "owner": "ou_1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "decided",
                           "decision": "approve"}
    assert broker.wait("ca1", timeout=0.1) == "approve"


def test_code_approval_owner_mismatch_forbidden(client_with_broker):
    """非发起者点击：forbidden，不写 broker。"""
    client, broker = client_with_broker
    resp = _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca2",
        "decision": "approve", "open_id": "ou_2", "owner": "ou_1",
    })
    assert resp.json() == {"ok": False, "status": "forbidden"}
    assert broker.wait("ca2", timeout=0.1) is None


def test_code_approval_deny_propagates(client_with_broker):
    """拒绝决策同样可达 coding 线程。"""
    client, broker = client_with_broker
    _post_card(client, {
        "action": "code_approval", "code_approval_id": "ca3",
        "decision": "deny", "open_id": "ou_1", "owner": "ou_1",
    })
    assert broker.wait("ca3", timeout=0.1) == "deny"


def test_code_approval_duplicate_click_idempotent(client_with_broker):
    """重复点击：幂等 already_handled，首决策不被覆盖。"""
    client, broker = client_with_broker
    _post_card(client, {"action": "code_approval", "code_approval_id": "ca4",
                        "decision": "approve", "open_id": "ou_1", "owner": "ou_1"})
    resp = _post_card(client, {"action": "code_approval", "code_approval_id": "ca4",
                               "decision": "deny", "open_id": "ou_1", "owner": "ou_1"})
    assert resp.json()["status"] == "already_handled"
    assert broker.wait("ca4", timeout=0.1) == "approve"
```

- [ ] **Step 5: /code 路由测试**

`tests/integration/test_message_flow.py` 末尾追加（模式与同文件 /research 路由测试一致：`orch` fixture + MagicMock runner + 直接构造 IncomingMessage）：

```python
# === Phase 26：/code 路由 ===


def test_code_command_routes_to_coding_runner(orch):
    """/code 指令进入 coding_runner.handle。"""
    orch.coding_runner = MagicMock()
    orch.coding_runner.handle.return_value = {"status": "coding_accepted"}
    msg = IncomingMessage(message_id="m1", chat_id="c1", sender_open_id="u1",
                          text="/code 写一个 hello.py 并运行")
    result = orch.process(msg)
    orch.coding_runner.handle.assert_called_once_with(msg)
    assert result["status"] == "coding_accepted"


def test_code_command_without_runner_replies_error(orch):
    """coding_runner 未配置：回错误提示，不抛异常。"""
    if hasattr(orch, "coding_runner"):
        del orch.coding_runner
    msg = IncomingMessage(message_id="m2", chat_id="c1", sender_open_id="u1",
                          text="/code 任意任务")
    result = orch.process(msg)
    assert result["status"] == "coding_unavailable"


def test_code_prefix_not_matched_by_plain_text(orch):
    """普通文本（非 /code 开头）不进 coding 分支。"""
    orch.coding_runner = MagicMock()
    msg = IncomingMessage(message_id="m3", chat_id="c1", sender_open_id="u1",
                          text="请帮我 /code 一下")   # 前缀不在行首
    orch.process(msg)
    orch.coding_runner.handle.assert_not_called()
```

注意：`orch` fixture 若已给 `coding_runner` 属性（后续 T7 装配），`test_code_command_without_runner_replies_error` 的 `del` 分支保持兼容；`test_code_prefix_not_matched_by_plain_text` 依赖现有 general_chat 路径——若该测试因 LLM mock 缺失而不稳定，改为断言 `result["status"] != "coding_accepted"` 即可。

- [ ] **Step 6: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_renew_card_callback.py tests/integration/test_message_flow.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS（既有用例不回归 + 新增 7 用例）

- [ ] **Step 7: commit**

```bash
git add config/settings.py gateway/app.py orchestrator/app.py tests/integration/test_renew_card_callback.py tests/integration/test_message_flow.py
git commit -m "feat(phase26): settings 七字段 + code_approval 回调分支 + /code 路由"
```

完成后删除 `.pytest_basetemp`，回总览勾选 T6。
