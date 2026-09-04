# Phase 29：skill 容器隔离 + 语义检索 + Working Memory 成功路径

日期：2026-09-04 晚。三项独立增强，均渐进兼容（不配即走旧行为）。

## T1 skill 容器隔离（可选 image 字段）

**现状**：`skill_loader._make_handler` 用 `subprocess.run` 本机直跑
（cwd=skill 目录），skill 脚本可无限制访问宿主（网络/文件系统/资源）。

**设计**：tools.yaml 工具条目新增可选 `image` 字段——
- 配了 image → `docker run --rm -i --network none --cpus 2 --memory 4g
  -v <skill_dir>:/skill:ro -w /skill <image> <command...> <args>`，
  超时/非零退出错误处理与本机模式一致（SCRIPT_ERROR + stderr 尾部）
- 未配 image → 本机直跑（现状不变，存量 skill 零迁移成本）

**限制（v1 明确范围）**：skill 目录只读挂载——需写宿主路径的 skill
（如生信脚本输出到 code_workspace）不适用 image，继续本机跑；
资源限额固定 cpus=2/memory=4g（与 skill 短命令场景匹配）。

**测试**：
1. image 工具注册后 handler 构造 docker argv（mock subprocess.run
   断言 argv 前缀 docker run --rm ... -v ...:/skill:ro -w /skill image ...）
2. 非 image 工具走本机 subprocess（现状回归）
3. docker 非零退出 → SCRIPT_ERROR + stderr
4. image 字段不进 schema/description（to_openai_function 不受影响）

## T2 skill 语义检索（LLM 选择 + 词元 fallback）

**现状**：`build_system_knowledge` 词元交集 v1——"质控"匹配不上
"QC"，skill 多起来后召回质量差。

**设计**：`build_system_knowledge(task_text, llm=None, top=3)`：
- llm 提供时：一次纯文本 chat，prompt = 任务 + 候选清单
  （name+description 每行一条）→ 要求返回 `{"skills": [name...]}`
  （仅限清单内 name，最多 top 个）；解析后按选中顺序拼知识块
  （skill body 截断 1500 仍复用）
- llm 为 None / LLM 异常 / 返回 name 不在清单 → 词元法 fallback
  （现有打分逻辑抽 `_rank_by_tokens` 复用），任何异常不抛出

**测试**：
1. LLM 选中 2 个 → 知识块按选中顺序、只含选中 skill
2. LLM 返回幻觉 name → 过滤；全幻觉 → fallback 词元法
3. LLM 异常 → fallback 词元法（不抛）
4. llm=None → 行为与现状完全一致（回归）
5. skill 清单空 → 返回 ""（不调 LLM）

**装配**：`coding_runner.run_sync` 传 `llm=self.llm`。

## T3 Working Memory 成功路径摘要

**现状**：memory 只记失败行（窗口 5），模型看不到"哪些调用已成功
可复用结果"，跨步重复跑昂贵成功调用（如大文件读取）。

**设计**（agent_loop.py）：
- 成功事件追加成功行：`- step N: name(args) → OK: <产出摘要>`
  （产出摘要 = obs 的 stdout/result 字段 str 截断 80；无则 "ok"）
- 消息结构扩展为两段（头不变，测试兼容）：
  `## 已试路径（避免重复尝试）` + `### 失败（勿重复）` 失败行（≤5，
  滚动同现状）+ `### 成功（可复用结果）` 成功行（≤3 滚动，最近优先）
- 失败行格式与 Phase 28 完全一致（既有测试零改动）

**测试**：
1. 成功调用后 memory 消息含 `OK:` 行与产出摘要
2. 成功行窗口 3（直测 _refresh_memory_message）
3. 失败+成功混合 → 两段都在，失败段格式不变
4. 无失败仅成功 → 只有成功段（仍单消息）

## 验收

- 全量回归（972 基线）+ 新用例全绿
- 真机（可选轻量）：/code 任务里确认 system 提示带语义选中 skill；
  bio skill 调用成功后第二次相似调用模型应引用记忆

## 排除

- 容器写宿主路径（挂 code_workspace——等真机需要再议）
- 嵌入向量检索（skill <50 个时 LLM 选择足够）
- 跨任务持久记忆（Working Memory 仍任务内）
