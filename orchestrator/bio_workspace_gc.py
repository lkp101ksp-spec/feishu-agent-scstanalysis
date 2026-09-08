"""Phase 23：bio_workspace 磁盘治理（spec §2）。

单次 sweep：筛 12hex 数据集目录 → last_used（打点文件/目录 mtime/直接子
文件 mtime 三者取大）→ 宽限期保护 → TTL 到期删除 → LRU 超 cap 驱逐。
纯函数式（now 可注入），不依赖 settings/ws_client，独立可测。
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class _SweepResult(TypedDict):
    """sweep 返回结构：TTL/LRU 删除名单 + 释放字节数 + 宽限跳过名单。"""
    ttl_deleted: list[str]
    lru_deleted: list[str]
    freed_bytes: int
    skipped: list[str]

# 数据集目录名 = compute_dataset_id{,_dir} 的输出（sha1 hexdigest[:12]）；
# smoke_st 等命名目录天然排除，无需白名单
_DATASET_DIR_RE = re.compile(r"^[0-9a-f]{12}$")


def _last_used(d: Path) -> float:
    """数据集最近使用时间：目录 mtime 与直接子文件（含 .last_access 打点）
    mtime 取最大——打点前的历史数据集靠文件/目录 mtime 兜底。"""
    candidates = [d.stat().st_mtime]
    for p in d.iterdir():
        if p.is_file():
            candidates.append(p.stat().st_mtime)
    return max(candidates)


def _dir_size(d: Path) -> int:
    """目录总字节数（递归）。"""
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def _delete(d: Path) -> int | None:
    """整目录删除并返回释放字节；失败 warning 返回 None（不中断 sweep）。"""
    try:
        size = _dir_size(d)
        shutil.rmtree(d)
        logger.info("bio workspace gc: deleted %s (freed %d bytes)",
                    d.name, size)
        return size
    except OSError as e:
        logger.warning("bio workspace gc: delete %s failed: %s", d.name, e)
        return None


def sweep(workspace_root, *, ttl_sec: int, cap_bytes: int, grace_sec: int,
          now: float | None = None) -> _SweepResult:
    """单轮清理：TTL 阶段删到期目录，LRU 阶段超 cap 按 last_used 升序驱逐。

    两阶段均跳过宽限期（grace_sec）内动过的目录（活动任务保护）。
    返回 {"ttl_deleted", "lru_deleted", "freed_bytes", "skipped"}。
    """
    root = Path(workspace_root)
    result: _SweepResult = {"ttl_deleted": [], "lru_deleted": [],
                            "freed_bytes": 0, "skipped": []}
    if not root.is_dir():
        return result
    now = time.time() if now is None else now

    survivors: list[tuple[Path, float]] = []
    for d in root.iterdir():
        if not (d.is_dir() and _DATASET_DIR_RE.match(d.name)):
            continue
        try:
            used = _last_used(d)
        except OSError as e:
            logger.warning("bio workspace gc: stat %s failed: %s", d.name, e)
            continue
        if now - used < grace_sec:
            result["skipped"].append(d.name)
            survivors.append((d, used))
            continue
        if now - used >= ttl_sec:
            freed = _delete(d)
            if freed is not None:
                result["ttl_deleted"].append(d.name)
                result["freed_bytes"] += freed
            else:
                survivors.append((d, used))
        else:
            survivors.append((d, used))

    # LRU：宽限期内的计入总量但不驱逐；驱逐到 ≤ cap 即止
    total = sum(_dir_size(d) for d, _ in survivors)
    evictable = sorted((x for x in survivors if now - x[1] >= grace_sec),
                       key=lambda x: x[1])
    for d, _ in evictable:
        if total <= cap_bytes:
            break
        size = _dir_size(d)
        freed = _delete(d)
        if freed is not None:
            result["lru_deleted"].append(d.name)
            result["freed_bytes"] += freed
            total -= size
    return result
