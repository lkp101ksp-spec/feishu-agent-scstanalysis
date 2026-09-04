# Phase 26 `/code` Agentic Coding Agent 实施计划 — 总览

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. 本 Phase 拆为 8 个任务文件，按序执行；每个文件内步骤用 checkbox 跟踪。

**Goal:** 为飞书 agent 增加 `/code` agentic coding 链路：自研 LLM function-calling 循环 + SKILL.md 兼容 skill 系统 + 会话工作区，与现有 `/research` DAG 链和 bio 工具链零破坏并存。

**Architecture:** 新增 `orchestrator/coding/` 模块（runner/agent_loop/code_tools/workspace/skill_loader 五件）+ `LLMRouter.chat_with_tools` 扩展（OpenAI tools 协议）+ `code_approval` 卡片回调分支。工具面三层拼装：CodeTools 原语 + skills/ 目录热加载工具 + 现有 ToolRegistry 白名单子集（默认 `sc_*`）。

**Tech Stack:** Python 3.12 / httpx（现有）/ pyyaml（T4 引入）/ pytest（mock LLM 驱动 TDD）。

**Spec:** `docs/superpowers/specs/2026-09-03-phase26-code-agent-design.md`

---

## 任务文件与执行顺序

| # | 文件 | 内容 | 依赖 |
|---|---|---|---|
| T1 ✅ | `2026-09-03-phase26-t1-llm-tools.md` | LLMRouter.chat_with_tools（function calling 协议 + fallback + strip_think） | 无 |
| T2 | `2026-09-03-phase26-t2-workspace.md` | WorkspaceManager + CommandPolicy（路径防逃逸 + 命令白/黑名单） | 无 |
| T3 | `2026-09-03-phase26-t3-code-tools.md` | CodeTools 六原语（read/write/edit/list/search/run_cmd） | T2 |
| T4 | `2026-09-03-phase26-t4-skill-loader.md` | SkillLoader（SKILL.md + tools.yaml 解析/注册/热加载/知识面匹配） | 无 |
| T5 | `2026-09-03-phase26-t5-agent-loop.md` | AgentLoop 状态机（步数/预算/超时三终止 + 截断压缩 + 连续失败禁用） | T1 |
| T6 ✅ | `2026-09-03-phase26-t6-wiring.md` | settings 七字段 + `code_approval` 卡片回调分支 + `/code` 路由 | 无 |
| T7 | `2026-09-03-phase26-t7-coding-runner.md` | CodingRunner（授权卡/后台线程/工具面拼装/过程反馈/结果卡）+ build_runtime 装配 | T1-T6 |
| T8 | `2026-09-03-phase26-t8-e2e-docs.md` | fake skill 集成冒烟 + 全量回归 + 真机验收清单 + 文档 | T1-T7 |

顺序执行 T1→T8（T2 与 T4 相互独立可并行）。

## 共享约定（所有任务遵守）

1. **pytest 命令**一律带 `--basetemp=.pytest_basetemp`（宿主 Temp 目录 PermissionError 规避），跑完删除该目录。
2. **新模块目录** `orchestrator/coding/`，包级 `__init__.py` 空文件在 T2 首次创建。
3. **接口契约速查**（跨任务一致，实现时不得改名）：
   - `LLMRouter.chat_with_tools(messages: list[dict], tools: list[dict], model: str | None = None) -> dict`（返回 `choices[0].message` dict，content 已剥 `<think>`）
   - `WorkspaceManager(root).session_dir(session_id) -> Path`；`resolve_safe(session_id, rel) -> Path`（越界抛 `PathEscapeError`）
   - `CommandPolicy.verdict(cmd: list[str]) -> tuple[str, str]`（`"allow"/"need_approval"/"block"`, reason）
   - `CodeTools(ws, session_id, *, approve_fn=None, cmd_timeout_sec=120)`；`.SCHEMA`（类属性）；`.dispatch(name, arguments) -> dict`（arguments 为 JSON 字符串或 dict）
   - `SkillLoader(skills_dir).scan() -> int`；`.register_tools(registry) -> int`；`.build_system_knowledge(task_text) -> str`
   - `AgentLoop(llm, tools_schema, dispatch, *, max_steps=25, token_budget=200000, timeout_sec=3600, risk_map=None, approve_fn=None, on_step=None, model=None).run(system, task) -> LoopResult`；`LoopResult(status, final_text, steps, approx_tokens, abort_reason="", tool_events=None)`（tool_events 为 `{step,name,ok}` 列表）
4. **commit 规范**：`feat(phase26): <内容>`，每任务至少一个 commit。
5. **依赖**：T4 引入 `pyyaml`（清华源安装）。
6. 完成一个任务后回到本文件勾选对应行。
