"""bio_trajectory_pipeline 编排脚本（Phase 60 建议③）。

按 Phase 53-59 最佳实践串五步（docker 调 bio 镜像内 sc_tools，
与 L3 同一份分析代码）：
  1. sc_pseudotime trajectory_full（全景：palantir 分支+slingshot 谱系+cross+paga）
  2. sc_meta list_cols（分组/轨迹列发现）
  3. sc_plot umap_obs（lineage_branch / slingshot_lineage 着色）
  4. sc_cellfreq celltype_col=lineage_branch（group 列存在时）
  5. steps=full 时 sc_cellchat celltype_col=lineage_branch

输入 --k v（skill_loader 展开布尔为旗标，本工具均为字符串参数）。
输出 bio_workspace/<ds>/trajectory_pipeline_summary.json。
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

WS_ROOT = Path("I:/飞书agent/bio_workspace")
IMAGE = "feishu-research-agent/bio:cpu-latest"
TOOLS = {
    "pseudotime": "/opt/sc_tools/pseudotime.py",
    "meta": "/opt/sc_tools/meta.py",
    "plot": "/opt/sc_tools/plot.py",
    "cellfreq": "/opt/sc_tools/cellfreq.py",
    "cellchat": "/opt/sc_tools/cellchat.py",
}


def run_tool(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    """docker 跑 sc_tools 单步，返回 emit 末行 JSON；失败抛 RuntimeError。"""
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--cpus",
            "4",
            "--memory",
            "16g",
            "-v",
            f"{WS_ROOT}:/ws",
            IMAGE,
            "python",
            TOOLS[tool],
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=3900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} 失败（exit {proc.returncode}）：{proc.stderr[-500:]}")
    line = next(
        (ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), "")
    if not line:
        raise RuntimeError(f"{tool} 无 emit 输出")
    out: dict[str, Any] = json.loads(line)
    if out.get("ok") is False:
        raise RuntimeError(f"{tool} 返回错误 {out.get('error_code')}：{out.get('error_message')}")
    return out


def main() -> int:
    args: dict[str, str] = {
        k.lstrip("-"): v for k, v in zip(sys.argv[1::2], sys.argv[2::2])}
    ds = args.get("dataset_id")
    root = args.get("root_cluster")
    if not ds or not root:
        print(
            "错误：dataset_id 与 root_cluster 必填。root_cluster 应为祖/干细胞簇"
            "（leiden 簇号）；未知时请先按 marker 推断并向用户确认，勿留空。"
        )
        return 2
    species = args.get("species", "human")
    steps = args.get("steps", "fast")
    summary: dict[str, Any] = {
        "dataset_id": ds,
        "root_cluster": root,
        "species": species,
        "steps": steps,
        "pipeline": [],
    }

    # 1) 轨迹全景（Phase 59 一键模式）
    out = run_tool(
        "pseudotime",
        {
            "dataset_id": ds,
            "trajectory_full": True,
            "root_cluster": root,
            "dyn_modules_k": 6,
            "modules_enrich": "go_bp",
            "paga": True,
            "paga_pt": True,
        },
    )
    pal, sling = out.get("palantir", {}), out.get("slingshot", {})
    summary["pipeline"].append(
        {
            "step": "trajectory_full",
            "ok": out.get("ok", True),
            "n_terminal": pal.get("n_terminal"),
            "n_lineages": sling.get("n_lineages"),
            "n_cross_sig": sling.get("n_cross_sig"),
            "cross_triggered_by": sling.get("cross_triggered_by"),
            "paga_edges": pal.get("n_paga_edges"),
            "branch_counts": pal.get("branch_counts"),
            "artifacts": {k: v for k, v in {**pal, **sling}.items() if k.endswith(("_csv", "_png"))},
        }
    )

    # 2) 列发现（只读）
    cols = run_tool("meta", {"dataset_id": ds, "op": "list_cols"})
    detail = cols.get("detail", {})
    group_cols = [g.get("col") for g in detail.get("group_cols", [])]
    summary["group_cols_found"] = group_cols
    summary["trajectory_cols_found"] = detail.get("trajectory_cols", [])

    # 3) 谱系/命运支 UMAP 着色（列存在才画）
    obs_cols = [
        c
        for c in ("lineage_branch", "slingshot_lineage")
        if c in (detail.get("trajectory_cols") or []) or c in group_cols
    ]
    if obs_cols:
        plot = run_tool("plot", {"dataset_id": ds, "kind": "umap_obs", "obs_cols": obs_cols})
        summary["pipeline"].append(
            {"step": "umap_obs", "ok": plot.get("ok", True), "pngs": plot.get("pngs", [])}
        )

    # 4) 命运偏向（存在 group 列时）
    group = args.get("group_col") or ("group" if "group" in group_cols else None)
    if group:
        cf = run_tool(
            "cellfreq", {"dataset_id": ds, "by": "sample", "group": group, "celltype_col": "lineage_branch"}
        )
        summary["pipeline"].append(
            {
                "step": "cellfreq",
                "ok": cf.get("ok", True),
                "fate_bias": cf.get("fate_bias"),
                "roe_csv": cf.get("roe_csv"),
            }
        )

    # 5) 支间通讯（full）
    if steps == "full":
        cc = run_tool("cellchat", {"dataset_id": ds, "celltype_col": "lineage_branch", "species": species})
        summary["pipeline"].append(
            {
                "step": "cellchat",
                "ok": cc.get("ok", True),
                "n_pairs": cc.get("n_pairs_tested"),
                "n_sig": cc.get("n_sig"),
                "top": cc.get("top", [])[:5],
                "csv": cc.get("csv"),
            }
        )

    out_path = WS_ROOT / ds / "trajectory_pipeline_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "summary_json": str(out_path),
                "steps_done": [s["step"] for s in summary["pipeline"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
