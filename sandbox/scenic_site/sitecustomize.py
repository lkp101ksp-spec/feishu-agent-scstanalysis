"""numpy>=1.24 移除的别名 shim（pyscenic 0.12.1 transform/diptest/rss 仍引用
np.object/np.float）。

置于 site-packages 由 site 模块自动导入——dask worker 子进程（spawn 出的
全新解释器）也会被覆盖，这是 scenic.py 进程内 shim 之外的兜底。"""
import numpy as np

for _alias, _typ in {"object": object, "float": float, "int": int,
                     "str": str}.items():
    if not hasattr(np, _alias):
        setattr(np, _alias, _typ)
