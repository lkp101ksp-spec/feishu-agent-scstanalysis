# st_misty PROGENy 通路视图设计（Phase 50）

日期：2026-09-11
状态：用户已批准（"好的，按这个方案来"；形态甲=扩 st_misty 加
extra_mode 参数；模型口径=OmniPath 14 通路）

## §1 定位与形态

Phase 49 st_misty 非目标留位"PROGENy/TF 通路视图"的落地：st_misty
扩 **`extra_mode`** 参数——`hvg`（默认，向后兼容）| `progeny`。
progeny 模式下 extra 视图从 HVG 表达换成 **PROGENy 14 通路活性分**
（decoupler MLM），回答"哪些细胞型与信号通路在空间上互相解释"。

- 同一工具、同一产物结构、SECTION_TITLES 37 项不变
- 容器脚本：`sandbox/st_tools/misty.py` 增 progeny 分支
- **镜像加两层**：decoupler pip 层 + PROGENy 模型快照层（§2）
- TF/collectri 视图仍留非目标（YAGNI）

## §2 API 事实（2026-09-11 两轮容器探针实测）

```python
import decoupler as dc                       # decoupler 2.2.0
net = dc.op.progeny(organism="human", top=500)
# DataFrame(6463, [source, target, weight, padj])，14 通路：
# Androgen/EGFR/Estrogen/Hypoxia/JAK-STAT/MAPK/NFkB/PI3K/TGFb/TNFa/
# Trail/VEGF/WNT/p53 —— 构建期联网拉取一次
dc.mt.mlm(adata, net, tmin=5, verbose=True)
# 返回 None；写 obsm["score_mlm"]（n_spots×14 DataFrame，列=通路）
# 与 obsm["padj_mlm"]；tmin=5=每通路最少 5 个交集基因（R run_mlm
# minsize=5 同款口径，tool-gene-program）
```

探针实测：

- `dc.mlm` **不存在**于 2.2.0，方法在 `dc.mt.mlm`（v2 命名空间）
- 断网 E2E 全绿：镜像内 TSV → MLM 无网络调用；50 spot×5276 基因
  约 10s；注入 EGFR 信号 corr=0.998 回收（次高 0.23）
- decoupler 2.2.0 装入 st 镜像仅多三个小纯 Python 包
  （adjusttext/legendkit/marsilea），无重依赖冲突

### 断网纪律：构建期快照模式（fetch_gene_pos 先例同构）

- **pip 层**：`pip install decoupler==2.2.0`（清华源）
- **快照层**：构建期 `dc.op.progeny(organism="human", top=500)` →
  `/opt/progeny/progeny_human_top500.tsv`
- 运行期只读镜像内 TSV，`--network none` 不变

## §3 数据流（extra_mode=progeny）

```
deconv.h5ad obsm["q05_cell_abundance_w_sf"] → intra（细胞型组成，目标）
/opt/progeny/progeny_human_top500.tsv       → net（构建期快照）
processed.h5ad 全基因表达（已 log1p）        ↓ dc.mt.mlm(tmin=5)
        obsm["score_mlm"]（spot×14 通路活性）→ extra（通路分，预测子）
        ↓ genericMistyData → juxta + para 视图（Phase 49 一字不改）
        ↓ RandomForestModel
uns["target_metrics"] / uns["interactions"] → csv + 热图（14 通路轴）
```

- 防御：net target ∩ adata.var_names < 100 → `INVALID_INPUT`
  （提示基因名非 symbol/物种不符）；100 为启发阈值——14 通路×
  top500 的 symbol 全集 5276 个，正常 Visium 交集数千，<100 即异常
- TSV 缺失（镜像层问题）→ `ST_MISTY_NO_PROGENY`
- progeny 模式 `n_hvg` 参数忽略（emit 回显 extra_mode 标明）

## §4 参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| dataset_ref | str | 必填 | 数据集 id |
| n_hvg | int | 50 | extra_mode=hvg 时 top HVG 数；progeny 模式忽略 |
| bandwidth | float | 0（auto） | para 视图半径；0 → 5×中位近邻距 |
| extra_mode | str | "hvg" | extra 视图来源：hvg ｜ progeny；非法值 INVALID_INPUT |

## §5 产物（落 `/ws/{ds}/misty/`，结构同 Phase 49）

- `misty_contributions.png` / `misty_interactions_para.png`（progeny
  模式预测子轴=14 通路）+ 两 csv
- emit 键同 Phase 49 + **`extra_mode` 回显**；progeny 模式
  `n_predictors`=14

## §6 错误码

| 码 | 场景 |
|---|---|
| ST_MISTY_NO_DECONV | deconv.h5ad 不存在（沿用） |
| ST_MISTY_NO_PROGENY | /opt/progeny TSV 缺失（镜像层问题，新增） |
| INVALID_INPUT | extra_mode 非法 / net∩var_names<100（新增两场景；沿用 n_hvg 越界 / bandwidth<0 / 交集 spot<100） |
| ST_FORMAT_INVALID | processed.h5ad 缺 obsm["spatial"]（沿用） |
| SCRIPT_ERROR | 兜底（沿用） |

## §7 测试

- **TDD 注册用例**（`tests/unit/test_l3_st_misty.py` 扩展）：args dict
  键集增 `extra_mode`（既有断言更新）；新增 2 例——extra_mode 默认
  "hvg"、extra_mode 透传进 args payload
- **断网容器冒烟**（`scripts/_smoke_st_misty.py` 增 progeny 段）：
  processed.h5ad var_names 用 PROGENy 真实基因名（builder 容器内读
  /opt/progeny TSV 取 target 基因）；EGFR 通路 target 按
  weight×tumor 梯度注入（教训十六：信号强度先行验证）→ 断言
  EGFR 进 Tumor para top3；错误路径：非法 extra_mode / 基因名零
  交集 INVALID_INPUT / NO_DECONV 沿用
- 镜像重建（decoupler+快照层实跑）+ 全量回归（`pytest -q -m
  "not pg"` 门禁口径，1265 基线 +2）+ pre-push 四道门 + CI 绿

## 非目标

- TF/collectri 调控子视图（decoupler 同法可扩，真实需求到位再议）
- hvg+progeny 双视图拼接（YAGNI；genericMistyData 单 extra 入口，
  拼接需额外对齐语义）
- PROGENy 打分独立工具/独立空间热图（st_misty 产物已覆盖解读）
- R progeny 包 11 通路口径对齐（已拍板 OmniPath 14 通路）
