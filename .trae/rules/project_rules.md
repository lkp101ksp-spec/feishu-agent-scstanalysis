# 项目规则

## 依赖锁定纪律（ADR-0028）

- `pyproject.toml` 增删依赖后，必须同步刷新 `requirements-lock.txt`（`.venv` 内 `pip freeze`，剔除 `-e ` 自引用行后整体替换），否则 CI 与本机漂移。
- 锁文件本机/CI 共用一份；安装方式：`pip install -r requirements-lock.txt` + `pip install -e . --no-deps`。
- 本地 venv 为 Windows：引入平台专属包（pywin32 等）时需手动给锁文件对应行加 `; sys_platform == 'win32'` 标记，否则 ubuntu CI 装不上。
