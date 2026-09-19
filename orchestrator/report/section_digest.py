"""D 报告汇编：sc_* 节点产物收集 + csv 摘要截断（纯函数，无副作用）。

产物提取泛化：图片取任意 .png 结尾字符串与 pngs 列表（与 csv 的
.csv 结尾判定对称），csv 取以 .csv 结尾的字符串输出，关键数字取
标量/短字符串/短列表（嵌套子 dict 递归下钻、键加前缀，trajectory_full
的 palantir/slingshot 全景产物可完整入报告）——新 sc 工具自动纳入。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.executor_types import ExecutionState

logger = logging.getLogger(__name__)

DIGEST_LIMIT = 4000
CSV_HEAD_LINES = 30

SECTION_TITLES: dict[str, str] = {
    "sc_load": "数据加载",
    "sc_qc": "数据质控",
    "sc_process": "数据质控与预处理",
    "sc_doublet": "双联体检测",
    "sc_annotate": "细胞类型注释",
    "sc_cellcycle": "细胞周期分析",
    "sc_markers": "标志基因分析",
    "sc_plot": "可视化绘图",
    "sc_enrichment": "富集分析",
    "sc_score_genes": "基因集打分",
    "sc_metabolism": "代谢活性分析",
    "sc_pseudotime": "拟时序分析",
    "sc_cytotrace2": "绝对干性打分（CytoTRACE2）",
    "sc_nichenet": "配体活性优先级（NicheNet）",
    "sc_de": "差异表达分析",
    "sc_subcluster": "亚群细分",
    "sc_integrate": "多样本整合",
    "sc_meta": "元数据注释",
    "sc_wnn": "多组学整合（WNN）",
    "sc_cellfreq": "细胞组成分析",
    "sc_milo": "细胞组成差异分析（Milo）",
    "sc_cnv": "CNV 恶性判定与亚克隆",
    "sc_cellchat": "细胞通讯分析",
    "sc_cellchat_v2": "细胞通讯分析（CellChat v2）",
    "sc_tcr": "免疫组库重建（TCR）",
    "sc_cytosig": "细胞因子信号预测（CytoSig）",
    "sc_genescore": "通路活性分析（PROGENy）",
    "sc_deconv": "空间反卷积",
    "sc_scenic": "转录调控网络",
    "sc_knockout": "基因敲除模拟",
    "st_load": "空间数据加载",
    "st_qc": "空间数据质控",
    "st_process": "空间数据预处理",
    "st_markers": "空间标志基因分析",
    "st_plot": "空间可视化",
    "st_domains": "空间结构域识别",
    "st_commot": "空间细胞通讯（COMMOT）",
    "st_cellchat_v2": "空间细胞通讯（CellChat v2）",
    "st_nichenet": "空间配体活性优先级（NicheNet）",
    "st_niche_scan": "全 niche NicheNet 配体扫描",
    "st_integrate": "多切片空间数据整合",
    "st_deconvolve": "空间反卷积",
    "st_stats": "空间统计分析",
    "st_cnv": "空间CNV推断",
    "st_niche": "空间生态位重构",
    "st_vicinity": "肿瘤邻域分层",
    "st_misty": "多视图空间建模",
    "st_trajectory": "空间拟时序",
}


@dataclass
class Section:
    """单个分析环节的产物集合（图/表/关键数字，宿主路径）。"""
    title: str
    tool_name: str
    images: list[str] = field(default_factory=list)
    csvs: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)


def container_to_host(container_path: str, ws_root: str) -> str | None:
    """容器 /ws/<rel> → 宿主 ws_root/<rel>；非 /ws 前缀返回 None。

    与 research_runner._container_to_host_path 语义逐字一致；本模块
    自带副本以避免 report 包反向依赖 research_runner（防循环导入）。
    """
    p = str(container_path).replace("\\", "/")
    if p.startswith("/ws/"):
        rel = p[len("/ws/"):]
    elif p.startswith("ws/"):
        rel = p[len("ws/"):]
    else:
        return None
    return str(Path(ws_root).resolve() / rel)


def _is_short_scalar(val: Any) -> bool:
    """关键数字候选：标量或 ≤80 字符的非路径字符串或短列表。"""
    if isinstance(val, bool) or isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        return not val.startswith(("/ws", "ws/")) and len(val) <= 80 \
            and not val.endswith((".png", ".csv", ".h5ad"))
    if isinstance(val, list):
        return (len(val) <= 12
                and all(isinstance(x, (str, int, float)) for x in val)
                and len(str(val)) <= 120)
    return False


def _harvest(outputs: dict[str, Any], section: Section, ws_root: str,
             prefix: str = "") -> None:
    """从 outputs（或嵌套子 dict）提取产物到 section（提取序=emit 序）。

    图片：任意 .png 结尾字符串 + pngs 列表（通用判定与 csv 对称，
    覆盖 branch_umap_png/paga_png/trend_heatmap_png 等非白名单键）；
    csv：.csv 结尾字符串；关键数字：短标量，嵌套键加前缀防撞
    （trajectory_full 的 palantir.n_terminal / slingshot.n_lineages）。
    值为 dict 时递归下钻（json emit 边界，无深嵌套风险）。
    """
    for key, val in outputs.items():
        full = f"{prefix}{key}"
        if isinstance(val, str) and val.endswith(".png"):
            host = container_to_host(val, ws_root)
            if host:
                section.images.append(host)
        elif isinstance(val, str) and val.endswith(".csv"):
            host = container_to_host(val, ws_root)
            if host:
                section.csvs.append(host)
        elif isinstance(val, list) and key == "pngs":
            for p in val:
                if isinstance(p, str) and p.endswith(".png"):
                    host = container_to_host(p, ws_root)
                    if host:
                        section.images.append(host)
        elif isinstance(val, dict):
            _harvest(val, section, ws_root, prefix=f"{full}.")
        elif _is_short_scalar(val):
            section.numbers[full] = val


def collect_sections(plan: Any, scheduler: Any, ws_root: str) -> list[Section]:
    """按 plan 节点顺序收集 SUCCESS 的 sc_*/st_* 节点产物。

    plan/scheduler 鸭型（DAGPlan/Scheduler；单测用 SimpleNamespace）；
    scheduler._handles 私有访问与 research_runner 既有先例一致。
    """
    tool_names = {n.node_id: n.tool_name for n in plan.nodes
                  if n.kind == "tool"}
    sections: list[Section] = []
    for node in plan.nodes:
        if node.kind != "tool":
            continue
        tool = tool_names.get(node.node_id) or ""
        if not tool.startswith(("sc_", "st_")):
            continue
        handle = scheduler._handles.get(node.node_id)
        if (handle is None or handle.state != ExecutionState.SUCCESS
                or not handle.outputs):
            continue
        section = Section(title=SECTION_TITLES.get(tool, tool),
                          tool_name=tool)
        _harvest(handle.outputs, section, ws_root)
        sections.append(section)
    return sections


def csv_digest(path: str, head_lines: int = CSV_HEAD_LINES) -> str:
    """读 csv 前 head_lines 行文本；文件缺失/读取失败记日志返回空串。"""
    try:
        lines: list[str] = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= head_lines:
                    break
                lines.append(line.rstrip("\n"))
        return "\n".join(lines)
    except OSError as e:
        logger.warning("csv digest read failed (%s): %s", path, e)
        return ""


def truncate_middle(text: str, limit: int = DIGEST_LIMIT) -> str:
    """超长文本头 45% 尾 30% 保留（agent_loop OBS_TRUNCATE 同款策略）。"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.45)
    tail = int(limit * 0.30)
    return (text[:head]
            + f"\n…（中间截断 {len(text) - head - tail} 字符）…\n"
            + text[-tail:])


def section_digest_text(section: Section) -> str:
    """拼单节 LLM 输入：关键数字 + 各 csv 头部摘要，整体 ≤DIGEST_LIMIT。"""
    parts: list[str] = []
    if section.numbers:
        kv = "；".join(f"{k}={v}" for k, v in section.numbers.items())
        parts.append(f"关键数字：{kv}")
    for path in section.csvs:
        text = csv_digest(path)
        if text:
            parts.append(
                f"数据表 {Path(path).name}（前 {CSV_HEAD_LINES} 行）：\n{text}")
    return truncate_middle("\n\n".join(parts))
