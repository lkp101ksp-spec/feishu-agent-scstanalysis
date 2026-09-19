"""_cytosig_core：CytoSig ridge 内核（Phase 71，跑在 /opt/cytosig_env）。

用法（venv python）：
  python _cytosig_core.py <expr.tsv> <out.tsv> <nrand>

expr.tsv：基因 × 样本 表达矩阵（行索引=基因符号，tab 分隔）；
out.tsv：因子 × 样本 宽表（beta/std/zscore/pvalue 三段堆叠，factor/
kind 列区分）。签名基因交集内核自取（find_signature_path 默认 43
因子），交集 <50 基因退出码 3。
"""
from __future__ import annotations

import sys

import pandas as pd


def main() -> None:
    """读 expr tsv → ridge_significance_test → 因子×样本结果 tsv。"""
    expr_path, out_path, nrand = sys.argv[1], sys.argv[2], int(sys.argv[3])
    import CytoSig

    sig_path = CytoSig.find_signature_path()
    signature = pd.read_csv(sig_path, sep="\t", index_col=0)
    # 签名索引 upper 化（C10orf88 类混合大小写符号）：上游 cytosig.py
    # 写出的 tsv 行索引已 upper，双侧一致才能全量交集；upper 撞名保首个
    signature.index = signature.index.str.upper()
    signature = signature[~signature.index.duplicated(keep="first")]
    Y = pd.read_csv(expr_path, sep="\t", index_col=0)
    Y_sub = Y.loc[Y.index.isin(signature.index)]
    if Y_sub.shape[0] < 50:
        print(f"signature overlap too low: {Y_sub.shape[0]} genes",
              file=sys.stderr)
        sys.exit(3)
    beta, std, zscore, pvalue = CytoSig.ridge_significance_test(
        signature, Y_sub, alpha=1e4, alternative="two-sided",
        nrand=nrand, cnt_thres=10, flag_normalize=True, verbose=False)
    frames = []
    for kind, df in (("beta", beta), ("std", std),
                     ("zscore", zscore), ("pvalue", pvalue)):
        long = df.reset_index().melt(id_vars="index", var_name="sample",
                                     value_name="value")
        long = long.rename(columns={"index": "factor"})
        long.insert(0, "kind", kind)
        frames.append(long)
    pd.concat(frames, ignore_index=True).to_csv(out_path, sep="\t",
                                                index=False)
    print(f"core ok: {beta.shape[0]} factors x {beta.shape[1]} samples, "
          f"{Y_sub.shape[0]} genes used, nrand={nrand}", file=sys.stderr)


if __name__ == "__main__":
    main()
