# Phase 27：Skill 失败诊断（半自动进化）实施计划

> spec 提炼自 Recuris 思想（不套框架）：/code 任务失败时自动分析轨迹、定位 skill 缺陷、生成改进建议，经人工审批卡写回 skill 目录。
> 前置：Phase 26 已收官（AgentLoop 三终止 + skill 热加载 + 审批卡闭环）。

## 目标

把 skill 生态从「手工编写静态配置」升级为「用中持续优化」：/code 任务失败时自动生成 skill 改进建议，人工批准后写回 SKILL.md/tools.yaml。

## 架构

```
AgentLoop.run() 结束
  ↓ status != final
SkillDiagnoser.diagnose(loop_result, skill_registry)
  ↓ LLM 分析 tool_events + 失败观察
改进建议（哪个 skill 什么问题 + 建议改法）
  ↓
审批卡（复用 code_approval 分支，action=skill_improve）
  ↓ 人工批准
SkillDiagnoser.apply(suggestion) 写回 skills/<name>/SKILL.md 或 tools.yaml
```

## 交付物（T1-T4）

### T1 SkillDiagnoser 核心

**文件**：`orchestrator/coding/skill_diagnoser.py`

```python
class SkillDiagnoser:
    """skill 失败诊断：轨迹分析 → 改进建议 → 审批写回。"""

    def __init__(self, llm, skills_dir: Path):
        self.llm = llm              # chat_with_tools 接口
        self.skills_dir = skills_dir

    def diagnose(self, loop_result: LoopResult, task_text: str) -> dict:
        """分析失败轨迹，返回改进建议 dict。
        返回 {"skill": "bio_preprocess", "issue": "...", "fix": "...", "file": "SKILL.md", "patch": "..."}
        或 {"ok": False, "reason": "no skill involved"}（无 skill 调用时跳过）
        """

    def apply(self, suggestion: dict) -> dict:
        """审批通过后写回 skill 文件（SKILL.md 追加改进段落或 tools.yaml 改参数）。"""
```

**诊断 prompt 模板**：
```
你是 skill 诊断专家。任务失败轨迹如下：
- 任务：{task_text}
- 终止原因：{status} / {abort_reason}
- 工具事件：{tool_events}（含失败观察）
- 已加载 skill：{loaded_skills}

请分析：
1. 哪个 skill/工具出问题（或无 skill 问题）
2. 具体问题（描述不清/参数缺失/命令错误/超时）
3. 改进建议（改 SKILL.md 描述 / tools.yaml 参数 / 增加示例）
4. 若改 SKILL.md，给出追加的 Markdown 段落；若改 tools.yaml，给出字段修改

以 JSON 返回：{"skill": "...", "issue": "...", "fix": "...", "file": "SKILL.md|tools.yaml", "patch": "..."}
```

### T2 审批卡集成

**改动**：`orchestrator/coding/coding_runner.py` 的 `run_sync` 末尾

```python
# Phase 27：任务失败时自动诊断 skill
if result.status != "final" and skill_tool_calls:
    suggestion = self.diagnoser.diagnose(result, task_text)
    if suggestion.get("skill"):
        self._send_skill_improve_card(incoming, suggestion)
        # 审批通过 → diagnoser.apply(suggestion)
```

**卡片**：复用 `_approval_card` 模板，action 改 `skill_improve`，value 内嵌 `suggestion`（JSON 字符串）。

**回调**：`gateway/app.py` 加 `skill_improve` 分支（与 code_approval 同模式，owner 比对 + broker.decide → 批准后 apply）。

### T3 写回逻辑

**SKILL.md 改进**：追加 `## 改进记录（{date}）` 段落 + suggestion 内容。
**tools.yaml 改进**：YAML load → 改字段（如 timeout_sec/description）→ dump 写回。

**安全**：写回前备份原文件（`SKILL.md.bak`），写回后 skill 热加载立即生效。

### T4 测试

**单测**（`tests/unit/test_coding_skill_diagnoser.py`）：
- diagnose 无 skill 调用 → `{"ok": False}`
- diagnose 有 skill 失败 → 返回 skill/issue/fix/file/patch 五字段
- apply SKILL.md → 追加改进段落 + .bak 备份
- apply tools.yaml → 字段更新 + .bak 备份

**集成**（`tests/integration/test_coding_e2e.py` 追加）：
- 任务失败（如 skill 命令错误）→ 自动诊断 → 发卡 → 模拟批准 → skill 文件更新

## 不做（v1 边界）

- 不做 Validation Gate 自动验证（无固定验证集）
- 不做 Working Memory（任务状态跟踪）——留给 Phase 28
- 不做跨任务 Skill Memory 进化（只改当前任务涉及的 skill）
- 不做 skill 自动创建（只改已有 skill）

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_skill_diagnoser.py -v --basetemp=.pytest_basetemp
.\.venv\Scripts\python.exe -m pytest tests/integration/test_coding_e2e.py -v --basetemp=.pytest_basetemp
```

## 验收

1. 故意让 skill 失败（如 bio_preprocess 的 input_path 传错）→ /code 任务失败 → 自动诊断发卡 → 批准 → SKILL.md 追加改进段落
2. 回归：Phase 26 全量回归 927 绿 + 新增约 10 用例

## 排期

后续再做（先完成 Phase 26 真机测试的 skill 全链验收）。
