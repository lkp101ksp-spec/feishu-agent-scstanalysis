"""ULID 封装，文件用下划线后缀避免与 python-ulid 包冲突。

兼容 python-ulid 4.x（timestamp 为 float 毫秒）与早期版本（datetime）。
"""
from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


def parse_ulid_timestamp(ulid_str: str) -> int:
    """从 ULID 字符串解析毫秒时间戳。"""
    raw = ULID.from_str(ulid_str).timestamp
    # 4.x：property 返回 float 毫秒
    if isinstance(raw, (int, float)):
        return int(raw)
    # 1.x：方法返回 datetime
    return int(raw().timestamp() * 1000)
