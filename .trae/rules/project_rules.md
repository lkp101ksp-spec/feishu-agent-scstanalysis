# 项目规则

## 依赖锁定纪律（ADR-0028）

- `pyproject.toml` 增删依赖后，必须同步刷新 `requirements-lock.txt`（`.venv` 内 `pip freeze`，剔除 `-e ` 自引用行后整体替换），否则 CI 与本机漂移。
- 锁文件本机/CI 共用一份；安装方式：`pip install -r requirements-lock.txt` + `pip install -e . --no-deps`。
- 本地 venv 为 Windows：引入平台专属包（pywin32 等）时需手动给锁文件对应行加 `; sys_platform == 'win32'` 标记，否则 ubuntu CI 装不上。

## 平台差异验证纪律（mypy 门禁，2026-09-08 沉淀）

- 本机是 Windows、CI 是 ubuntu：凡改动涉及平台专属符号（`ctypes.WinDLL`、`get_last_error`、路径盘符语义等），仅在本机跑门禁存在平台盲区，必须加 `python -m mypy --platform linux` 反模拟（或直接推 CI 验证）。
- 平台分支必须写成 `if sys.platform == "win32":` 语句块——三元表达式不触发 mypy 的可达性特判，双平台都会检查。

## 大库真机验证纪律（2026-09-17 用户口径）

- 真机回归/对照/新功能验证遇到大库（如 59900 细胞级），**一律先随机 subset ~1000 细胞子集数据集再测**（固定种子、保留 obs/obsm），不直接在全量上反复烧；子集验证通过后如需全量结论再单独跑。
- 参考实现：`bio_workspace/pt1ksmoke`（seed=7，从 50165a559c91_bbknn 抽取）。
