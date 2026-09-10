"""D 分析报告自动汇编（Phase D）。

流程收尾自动收集 sc_* 节点产物 → LLM 逐节解读 → Markdown 底稿 +
飞书云文档交付。入口 maybe_build_report 见本包 __init__（Task 3 追加）。
"""
