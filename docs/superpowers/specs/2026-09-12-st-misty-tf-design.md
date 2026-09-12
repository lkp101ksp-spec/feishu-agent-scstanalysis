# st_misty TF/collectri 调控子视图设计（Phase 52）

日期：2026-09-12
状态：用户已批准（"按这个方案来"；沿用 Phase 50 extra_mode 扩展模板，
第三值 `tf`）

## §1 定位与形态

Phase 50 非目标留位"TF/collectri 调控子视图"的落地：st_misty
`extra_mode` 扩第三值——`hvg`（默认）｜ `progeny` ｜ **`tf`**。
tf 模式下 extra 视图换成 **CollecTRI TF 活性分**（decoupler MLM，
1185 TFs 经 tmin=5 过滤后 ~772 个），回答"哪些细胞型与转录因子
调控子在空间上互相解释"。

- 同一工具、同一产物结构、SECTION_TITLES 38 项不变
- 容器脚本：`sandbox/st_tools/misty.py` 增 tf 分支（progeny 分支同构）
- **镜像加一层**：collectri 模型快照层（pip 零改动，decoupler 已在镜像）
- 与 progeny 的唯一规模差异：预测子 14 → ~772，需 top-N 截断（§5）

## §2 API 事实（2026-09-12 两轮容器探针实测）

```python
import decoupler as dc                       # decoupler 2.2.0
net = dc.op.collectri(organism="human")
# DataFrame(42990, [source, target, weight, resources, references,
# sign_decision])，1185 TFs、6675 唯一靶基因 —— 构建期联网拉取一次
dc.mt.mlm(adata, net, tmin=5, verbose=False)
# 返回 None；写 obsm["score_mlm"]（n_spots×n_TFs DataFrame，列=TF）
```

探针实测（`.probe_tf/probe_a_fetch.py` / `probe_b_e2e.py`）：

- 抓取 API = `dc.op.collectri`（无 `dc.get_collectri` 回退必要）
- 断网 E2E 全绿：镜像内 TSV → MLM 无网络调用；400 spot×6675 基因
  MLM 11.8s；注入 DDIT3（100 靶基因 weight×梯度）活性 corr=0.9901
  回收，且为高梯度端 top1 TF（次高 0.76，区分度 >14×）
- 772 TFs 通过 tmin=5（6675 靶基因全交集时）
- **秩约束坑**：overlap 仅 100 时 MLM 断言失败（sources > unique
  targets，邻接矩阵秩 < 协变量数）→ overlap 阈值设 **500**（真实
  Visium 交集数千，500 留足余量）
- MISTy 耗时外推：单 RF（400×772 特征）4s → 12 靶 × 3 视图 ≈150s，
  timeout 1800s 充足

### 断网纪律：构建期快照模式（第三例复用）

- **pip 层**：零改动（decoupler==2.2.0 Phase 50 已装）
- **快照层**：构建期 `sandbox/fetch_collectri.py` 跑
  `dc.op.collectri(organism="human")` → `/opt/collectri/collectri_human.tsv`，
  断言 TFs>1000
- 运行期只读镜像内 TSV，`--network none` 不变

## §3 数据流（extra_mode=tf）

```
deconv.h5ad obsm["q05_cell_abundance_w_sf"] → intra（细胞型组成，目标）
/opt/collectri/collectri_human.tsv          → net（构建期快照）
processed.h5ad 全基因表达（已 log1p）        ↓ dc.mt.mlm(tmin=5)
        obsm["score_mlm"]（spot×~772 TF 活性）→ extra（TF 分，预测子）
        ↓ genericMistyData → juxta + para 视图（Phase 49 一字不改）
        ↓ RandomForestModel
uns["target_metrics"] / uns["interactions"] → csv（全量）+ 热图（top30 截断）
```

- 防御：net target ∩ adata.var_names < 500 → `INVALID_INPUT`
  （提示基因名非 symbol/物种不符；秩约束实证 100 即崩）
- TSV 缺失（镜像层问题）→ `ST_MISTY_NO_COLLECTRI`
- tf 模式 `n_hvg` 参数忽略（emit 回显 extra_mode 标明）

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| n_hvg | int | 50 | extra_mode=hvg 时 top HVG 数；progeny/tf 模式忽略 |
| bandwidth | float | 0（auto） | para 视图半径；0 → 5×中位近邻距 |
| extra_mode | str | "hvg" | extra 视图来源：hvg ｜ progeny ｜ tf；非法值 INVALID_INPUT |

## §5 产物（落 `/ws/{ds}/misty/`，结构同 Phase 49/50）

- `misty_contributions.png`（不变，行=target）
- `misty_interactions_para.png`：**tf 模式只画 para importance 总和
  top 30 预测子**（772 列不可读）；progeny/hvg 模式不截断（≤500 列
  可读性可接受，progeny 仅 14）
- 两 csv **全量不截断**（下游可自查任意 TF）
- emit 键同 Phase 50 + `n_predictors_total`（tf 模式=score_mlm 列数）
  与 `n_predictors_shown`（tf 模式=30，其他=n_predictors）

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_MISTY_NO_DECONV | deconv.h5ad 不存在（沿用） |
| ST_MISTY_NO_PROGENY | /opt/progeny TSV 缺失（沿用） |
| ST_MISTY_NO_COLLECTRI | /opt/collectri TSV 缺失（镜像层问题，新增） |
| INVALID_INPUT | extra_mode 非法（提示改 hvg｜progeny｜tf）/ collectri net∩var_names<500（新增两场景；沿用既有场景） |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（沿用） |
| SCRIPT_ERROR | 兜底（沿用） |

## §7 测试

- **TDD 注册用例**（`tests/unit/test_l3_st_misty.py` 扩展）：新增 2
  例——extra_mode="tf" 透传进 args payload、enum 描述含 tf（既有
  非法值拒绝用例如已覆盖三值之外则不动）；14 工具计数不变
- **断网容器冒烟**（`scripts/_smoke_st_misty.py` 增 tf 段）：DS_TF
  数据集 processed.h5ad var_names 用 collectri 真实基因名（builder
  容器内读 /opt/collectri TSV）；DDIT3 靶基因按 weight×tumor 梯度
  注入（探针 B 同款配方）→ 断言 tf 段 ok、n_predictors_total>500、
  para csv 全量中 DDIT3 importance 排名进前 30；错误路径：基因名
  零交集 INVALID_INPUT；NO_COLLECTRI 路径同 NO_PROGENY 策略（快照
  在镜像内无法模拟缺失，不覆盖）
- 镜像重建（快照层实跑）+ 全量回归（`pytest -q -m "not pg"` 门禁
  口径，1272 基线 +2）+ pre-push 四道门 + CI 绿

## 非目标

- TF 活性独立空间热图/独立打分工具（st_misty 产物已覆盖解读）
- progeny+tf 双视图拼接（YAGNI；genericMistyData 单 extra 入口）
- collectri sign_decision/resources 列的额外利用（weight 已含符号）
- mouse collectri（organism 参数化留待真实需求）
