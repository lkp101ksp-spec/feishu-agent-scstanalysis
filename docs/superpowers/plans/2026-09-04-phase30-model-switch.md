# Phase 30：可视化模型 API 切换（飞书管理卡）

日期：2026-09-04 ｜ 状态：实施中

## 背景与目标

现状：LLM 主备模型在 `.env`（6 变量）+ `config/llm.yaml`（声明 env 名）静态配置，
`build_runtime` 构造 `LLMRouter` 时固化两个 `_Provider`，改模型必须改文件 + 重启。

目标：管理员在飞书发 `/model` → 状态卡（当前主备 + 候选池按钮）→ 点按钮**热切换
（无需重启）**，DB 持久化（重启后仍生效），全量审计，key 全程不回显。

用户决策（AskUserQuestion 已对齐）：飞书管理卡 / 主备整体切换 / 预置候选池。

## 设计决策

### D1 候选池 = llm.yaml `providers:` 段，active 状态 = DB（key 不落库不进消息流）

- `config/llm.yaml` 新增可选 `providers:` 列表（name + `base_url_env`/`api_key_env`/
  `model_env`，沿用现有 env 间接引用模式）。存量两候选（primary/fallback）直接
  引用现有 `LLM_PRIMARY_*`/`LLM_FALLBACK_*` 变量名，**存量部署零改动**。
- 新表 `llm_active`（单行：`primary_name`/`fallback_name`/`updated_at`）只存
  **名字**；api key 只存在于 `.env` 与进程内存，不进 DB、不进卡片。
- 启动优先级：DB 有 active 且名字能在 providers 解析 → 用之；否则回退 `router:` 段
  （现状行为，向后兼容）。
- 加新候选 = yaml 加一段 + .env 加三个变量（决策：不做 /model add 动态加候选，
  避免 key 流经 IM 消息）。

### D2 热切换 = LLMRouter.reconfigure() 原地替换

```python
def reconfigure(self, primary: dict, fallback: dict, max_retries=None) -> None:
    self.primary = _Provider(**primary)
    self.fallback = _Provider(**fallback)
```
LLMRouter 实例被 orchestrator/research_runner/coding_runner 等共享引用，原地替换
属性即可全局生效，无需动装配。引用赋值原子，两行间微小窗口（新主+旧备）可接受
（管理级低频操作，旧备也是有效配置）。

### D3 ModelSwitchService（新文件 orchestrator/model_switch_service.py）

依赖：`llm`（LLMRouter）、`providers`（list[ProviderConfig]）、`admin_ids`（set）、
`session_factory`。职责内聚命令与回调两侧：

- `status_card(open_id) -> dict | None`：非 admin 返回 None（命令侧回复无权限提示）；
  admin 返回卡片 JSON（当前主备 name/model/base_url host + 每候选 [设为主][设为备]
  按钮，value 平铺 `action=model_switch, name, slot`）。**断言卡片序列化结果不含
  任何 api_key 值**（测试铁律）。
- `switch(open_id, name, slot) -> dict`：admin/name∈providers/slot∈{primary,fallback}
  三重校验 → repo upsert → `llm.reconfigure(...)` → 返回 `{ok, slot, from, to}`；
  失败 `{ok: False, reason}`。主备可同名（允许，自由度给管理员）。

### D4 命令挂载：orchestrator/app.py process() 1.69（/code 之后）

`/model` → admin 校验 → `im.send_card(status_card)`；非 admin → 文本提示 +
审计 `llm_model_switch_denied`。群聊门控（1.7）在命令分支之后，无需特判。

### D5 回调挂载：gateway/app.py process_card_payload 加 `action == "model_switch"`

仿 skill_improve 分支：公共段 `_audit_event`（target_type=`llm_config`，
target_id=`{slot}:{name}`，detail: slot/from/to/reason）→ 成功 action=
`llm_model_switched`，失败（非 admin/幻觉 name）action=`llm_model_switch_denied`。
toast：card_result_to_response 加 `model_switch` 状态分支（成功/无权限/无效候选）。

### D6 admin 名单：复用 .env `FEISHU_ADMIN_OPEN_IDS`（实施修正）

与模板审核（runtime.py public_service）共用一份管理员概念，不另设变量；
runtime 装配处解析为 set 传入 service。空名单 = 无人可切（安全默认）。

### D7 迁移 0005_llm_active：建表即可（单行由 repo upsert 首写创建）

## 文件清单

| 文件 | 变更 |
|---|---|
| config/llm.yaml | + providers 段（primary/fallback 两条引用现有 env） |
| .env.example | + ADMIN_OPEN_IDS 注释示例 |
| config/settings.py | LLMSettings + providers（tuple，缺省 ()）；Settings + admin_open_ids |
| orchestrator/llm_router.py | + reconfigure() |
| orchestrator/model_switch_service.py | 新建 |
| orchestrator/app.py | + /model 分支（1.69） |
| gateway/app.py | + model_switch 回调分支 + 审计 |
| gateway/ws_client.py | + toast 分支 |
| gateway/runtime.py | 装配 service + 启动时 DB active 应用（reconfigure） |
| persistence/models.py | + LLMActiveRow |
| persistence/repositories/llm_active_repo.py | 新建（get/upsert） |
| migrations/versions/0005_llm_active.py | 新建 |

## 测试清单（TDD）

单测 `tests/unit/test_model_switch_service.py`：
- admin 通过切换：DB upsert 落行 + router.primary.model 变更（mock LLMRouter 真实例）
- 非 admin 拒绝 / name 幻觉拒绝 / slot 非法拒绝（reason 语义）
- status_card：不含 api_key 值（序列化全串断言）、当前主备标注、按钮 value 形状
- 主备同名允许
- settings：providers 段解析 + 缺省 ()；ADMIN_OPEN_IDS 逗号解析
- llm_router：reconfigure 后同实例引用生效

集成 `tests/integration/test_renew_card_callback.py` 补：
- model_switch admin ok → toast + 审计 llm_model_switched（target_id 形状）
- 非 admin → denied 审计，不落 active
- 幻觉 name → denied 审计

真机验收（需用户配合点击）：
1. 非 admin 账号 /model → 拒绝提示 + denied 审计
2. admin /model → 卡片（无 key）
3. 点 [设为主] → toast 成功 → /model 复查已切换 → 发任务验证新模型应答
4. `scripts\start.ps1` 重启 → /model 仍显示切换后（DB 持久化）
5. 查审计表 4 条记录

## 排除项

- Web 管理页（决策不做——ws_client 需内嵌 HTTP 服务，工程量 2-3 倍）
- 按角色分别切模型（远期：LLMRouter.call 的 role 标签已预留）
- /model add 动态加候选（key 不进消息流，加候选走 yaml+.env）
- LLM 失败自动切换候选池（现 fallback 机制已覆盖单点故障）
