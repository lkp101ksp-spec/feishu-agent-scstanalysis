# Phase 24：planner repr 串执行层纠正 设计

日期：2026-09-02
状态：已批准（用户选定方案 A：执行层 schema 驱动纠正 + warning 日志 + prompt 源头减量）

## 背景

planner（LLM）输出的 plan JSON 整体合法，但个别参数被二次编码成 Python repr
字符串（如 `"genes": "['A','B']"`）——`json.loads` 层面查不出，字符串原样
流进 handler 才炸。真机已多次踩坑，既有点修 4 处均为同一模式的复制粘贴：

| 位置 | 处理对象 |
|---|---|
| `orchestrator/blocks/serializer.py:55` | write_doc blocks（单引号 repr） |
| `orchestrator/tools/bio/bio_runner.py:62` parse_gene_list | sc_*/st_* genes |
| `orchestrator/planner/scheduler.py:92` _parse_literal | 上下文值（条件判断） |
| `orchestrator/executor/kernel_manager.py:46` | 沙箱参数 |

新工具加 list/dict 参数就得记得再贴一次——本条 ROADMAP 遗留"planner repr
串长期方案"即为此立项。

## 决策记录（用户确认）

| 决策点 | 选择 | 落选项 |
|---|---|---|
| 修复层级 | 方案 A：执行层（ToolHandler.execute）schema 驱动纠正 + planner prompt 源头减量 | B：纯 planner 源头治理（依赖 provider 能力、直呼无保护）；C：公共工具函数手动接入（不自动） |
| 纠正可观测 | 纠正成功记 `logger.warning`（追踪 LLM 复发率） | 静默纠正 |
| 纠正范围 | 仅 schema 声明 array/object 且收到 str 的参数 | integer/number/boolean 的 str 纠正（YAGNI，真机未见翻车） |

## §1 架构与组件

```
planner plan JSON → DAGNode.params → ToolHandler.execute()
                                       └─ coerce_params(schema, inputs)   # 新
                                            ├─ 纠正 → warning 日志
                                            └─ 放过 → 原样流转
                                       → spec.handler(**inputs)
```

- **新模块 `orchestrator/tools/param_coerce.py`**：
  `coerce_params(parameters_schema: dict, inputs: dict) -> tuple[dict, list[str]]`
  纯函数——返回（纠正后的 inputs 副本，被纠正的参数名列表）；不依赖
  ToolHandler，独立可测；内部全 try/except 兜底，任何异常原样放过。
- **tool_handler.py `execute()`**：在 AST 检查之后、`spec.handler(**inputs)`
  之前调用；有纠正时
  `logger.warning("tool %s param coerced from repr-string: %s", tool_name, names)`。
- **planner prompt 源头减量**：`orchestrator/planner/planner.py` `_build_prompt`
  输出格式段（第 127 行"输出格式（严格遵循…）"句后）追加：
  `"inputs 中数组/对象类型参数必须输出真 JSON 数组/对象，不要用字符串包裹的 Python repr。"`

## §2 纠正算法（coerce_params）

1. 遍历 `parameters_schema.get("properties", {})`，取声明 `type` 为
   `"array"` 或 `"object"` 的参数名。
2. inputs 中该参数值是 **str 且 strip 后非空** → 依次尝试
   `json.loads` → `ast.literal_eval`；解析结果类型须与声明匹配
   （array→list，object→dict）才算纠正成功。
3. 都失败或类型不匹配 → 原样保留（交给 handler/下游既有校验照常报错，
   不掩盖真错误）。
4. 声明 `integer/number/boolean/string` 的参数一律不动（YAGNI）。
5. 与既有点修幂等共存：parse_gene_list / blocks serializer 收到的已是真
   list/dict 时行为不变（纠正器只动 str 值）。

## §3 错误处理

- coerce_params 内部：schema 缺 properties、值不可解析、literal_eval 抛
  任何异常 → 一律原样保留该参数，绝不向上抛。
- execute() 集成：coerce_params 整体异常（防御性 try/except 包裹调用点）
  → 记 warning 并用原 inputs 继续执行。

## §4 测试（TDD）

- `tests/unit/test_param_coerce.py`（9 用例）：
  1. array 单引号 repr 串 → list
  2. array 合法 JSON 串 → list
  3. object repr 串 → dict
  4. 无法解析的 str → 原样保留
  5. 类型不匹配保留（声明 array 但解析出 dict）
  6. 非 array/object 声明（string/integer）不动
  7. 已是真 list/dict 不动（幂等）
  8. 空串 / 空白串不动
  9. schema 无 properties 键 → 原样返回不炸
- execute() 集成用例（追加到 `tests/unit/test_tool_handler_blocks.py`）：
  repr genes 进 execute → handler 收到真 list + 触发 warning 日志
  （caplog 断言）。
- prompt 改动无单测（文案），真机验收观察。

## §5 验收标准

- 全量回归 passed（830 + 新增 ≥10）
- 真机 /research 一轮带 genes 参数的 sc/st 链路无回归；若 planner 再出
  repr 串，ws_client 日志可见 `param coerced from repr-string` warning
- ROADMAP：持续项勾掉 planner repr 串 + 变更记录

## 非目标（YAGNI）

- 不做 integer/number/boolean 的 str→值纠正
- 不替换/删除既有点修（幂等共存，后续自然腐化时再清）
- 不上 structured output / function calling（依赖 provider，留远期池）
