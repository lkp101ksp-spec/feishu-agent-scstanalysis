# Phase 23：bio_workspace 磁盘治理 设计

日期：2026-09-02
状态：已批准（用户确认 TTL+LRU 混合 / 周期扫描线程 / 宽限期保护 / 打点标记方案 A）

## 背景

Phase 20/21 引入 bio 短命容器架构后，每个数据集在 `bio_workspace/<dataset_id>/`
沉淀 raw/filtered/processed h5ad + PNG 产物（tiny 数据约 27MB/数据集，真实
Visium/scRNA 数据可达 GB 级）。当前 5 个数据集共 137MB，随真机使用持续增长，
无任何回收机制（ROADMAP Phase 20 后续扩展 #2）。

`dataset_ref = sha1(路径+大小)[:12]` 是幂等键——数据集被删后旧 ref 再引用走
现有 "dataset not found" 错误链，用户重新 sc_load/st_load 即可满血重建，
因此删除是安全可恢复的。

## 决策记录（用户逐项确认）

| 决策点 | 选择 | 落选项 |
|---|---|---|
| 清理策略 | TTL + LRU 混合 | 纯 TTL / 纯 LRU |
| 触发方式 | ws_client 周期扫描线程（沿用 kernel idle sweeper 模式） | load 时顺带 / 手动命令 |
| 活动任务保护 | 宽限期（2h 内动过的一律不删） | 活动引用名单（耦合 session 内部） |
| last_used 追踪 | 打点标记文件 `.last_access`（方案 A） | 只看 mtime（只读引用丢失）/ manifest JSON（YAGNI） |

## §1 架构与组件

```
ws_client main()
  └─ start_bio_workspace_gc_sweeper(bio_runner/settings)   # 新，daemon 线程
        └─ 每 interval 秒调用 bio_workspace_gc.sweep(...)   # 新模块，纯函数式

BioRunner.run()                                           # 改：开头打点
  └─ touch <workspace>/<dataset_id>/.last_access（best-effort）
```

- **新模块 `orchestrator/bio_workspace_gc.py`**：`sweep(workspace_root, *,
  ttl_sec, cap_bytes, grace_sec, now=None) -> dict` 纯函数式服务（`now` 可注入
  便于单测），返回 `{"ttl_deleted": [...], "lru_deleted": [...],
  "freed_bytes": int, "skipped": [...]}`。不依赖 settings/ws_client，独立可测。
- **BioRunner.run() 打点**：`args.get("dataset_id")` 存在且对应目录存在时，
  创建/utime `.last_access`；任何异常仅 `logger.warning`，不阻断任务。
  （load 脚本的目标目录由容器内脚本新建，打点自然跳过——新目录 mtime
  本来就是最新的，无需特殊处理。）
- **ws_client 线程**：`start_bio_workspace_gc_sweeper()` 完全沿用
  `start_kernel_idle_sweeper` 模式（daemon、命名线程、单轮异常吃掉保线程、
  启动日志 `"bio workspace gc sweeper started (interval=...s, ttl=...cap=...)"`），
  在 main() 现有 scanner 启动块旁挂接；`enabled=False` 或无 bio_runner
  （引擎未装配 bio 工具）时不启动。

## §2 清理算法（单次 sweep）

1. **候选集**：workspace 一级子目录中名字匹配 `^[0-9a-f]{12}$` 的目录
   （compute_dataset_id/compute_dataset_id_dir 的输出格式）。`smoke_st` 等
   命名目录天然排除，无需白名单。
2. **last_used 计算**：`max(.last_access 的 mtime（存在时）, 目录 mtime,
   直接子文件 mtime 的最大值)`。取三者最大是为了兼容打点前的历史数据集。
3. **宽限期**：last_used 距今 < `grace_sec`（默认 2h）→ 跳过（TTL/LRU
   两阶段均遵守）。运行中的任务要么在写产物（文件 mtime 新）、要么读引用
   （BioRunner 打点新），宽限期由此兜住。
4. **TTL 阶段**：last_used 早于 `now - ttl_sec`（默认 7d）→ `shutil.rmtree`
   整目录删除。
5. **LRU 阶段**：TTL 后统计剩余总量，超过 `cap_bytes`（默认 10GB）→
   按 last_used 升序驱逐（跳过宽限期内的）直到 ≤ cap。
6. 每次删除记 INFO（目录名、释放字节、触发阶段）；单目录删除失败
   `logger.warning` 并继续下一个；总量统计用 `rglob` 求和（5 个数量级的
   目录规模下开销可忽略）。

## §3 配置（config/settings.py，沿用 bio_* 命名与 env 覆盖模式）

| 字段 | 默认 | env |
|---|---|---|
| `bio_workspace_ttl_sec` | 604800（7d） | `BIO_WORKSPACE_TTL_SEC` |
| `bio_workspace_cap_gb` | 10 | `BIO_WORKSPACE_CAP_GB` |
| `bio_workspace_grace_sec` | 7200（2h） | `BIO_WORKSPACE_GRACE_SEC` |
| `bio_workspace_gc_interval_sec` | 3600 | `BIO_WORKSPACE_GC_INTERVAL_SEC` |
| `bio_workspace_gc_enabled` | true | `BIO_WORKSPACE_GC_ENABLED`（"false"/"0" 关闭） |

## §4 错误处理

- 打点失败：warn 不阻断（宁可漏打点不可挂任务）。
- sweep 单目录删除失败：warn 继续；整轮异常：sweeper 线程吃掉，下周期自愈。
- 误删恢复：数据集幂等键不变 → 重新 sc_load/st_load 秒回；这不是错误态，
  是设计内的可恢复行为。

## §5 测试（TDD）

- `tests/unit/test_bio_workspace_gc.py`（tmp_path 构造假数据集，`now` 注入
  控制时间）：
  1. TTL 到期删除 / 未到期保留
  2. LRU 超 cap 按 last_used 升序驱逐、达标即停
  3. 宽限期保护（TTL/LRU 两阶段各一例）
  4. last_used 取三者最大（.last_access 新于目录 mtime 时以打点为准）
  5. 非 12hex 目录（smoke_st）跳过
  6. 单目录删除失败（monkeypatch rmtree 抛错）不影响其他目录
  7. BioRunner 打点：dataset_id 目录存在则 .last_access mtime 更新；
     目录不存在/异常不阻断 run
- sweeper 线程：enabled=False 不启动、正常启动日志（沿用现有 scanner
  测试模式，monkeypatch sleep 单轮验证）。
- 收尾真机验证：手动造一个 mtime 超期的假 12hex 目录，等一轮 sweep
  （或临时调小 interval）确认删除日志。

## §6 验收标准

- 全量回归 passed（811 + 新增 ≥9）
- 真机：造超期假目录 → sweeper 删除并记日志；活动数据集不受影响
- 冒烟 `smoke_st_chain.py` 全链 8 步 PASS（确认打点未影响 bio 链路）
- ROADMAP：Phase 20 后续扩展勾掉磁盘治理 + 新增 Phase 23 节 + 变更记录

## 非目标（YAGNI）

- 不做引用链追踪/manifest 元数据
- 不动 `bio_test_data`（原始输入数据只读挂载，不属于 workspace 治理范围）
- 不做 bio_workspace 之外的产物回收（Phase 16 workspace 产物回收是另一条线）
