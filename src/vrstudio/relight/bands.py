"""按行带把逐像素任务分给线程池；结果与整帧计算逐位相同。

切带只是一次索引划分：每个行带内的运算顺序、数组形状（除行数外）与整帧计算一致，
所以 LUT 查表、逐项乘加、求幂、截断量化都不会因为切带改变结果。

本模块不保存任何线程、缓冲或缓存。线程池由调用方在一次渲染开始时创建，
随 ``with`` 退出时关闭并 join；串行路径传 ``pool=None``。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

#: 留给 ffmpeg 解码、x264 编码与系统的逻辑核数。
RESERVED_CORES = 4
#: 行带线程的上限：8/16/32 线程的实测差异在 10% 以内，再往上只增加争用。
MAX_WORKERS = 16


def workers_for(cpu_count: int | None = None) -> int:
    """按机器推导行带线程数，不设配置项；下限 1 表示串行。"""
    total = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, min(MAX_WORKERS, total - RESERVED_CORES))


def band_edges(height: int, workers: int) -> list[tuple[int, int]]:
    """把 ``height`` 行切成不超过 ``workers`` 段，边界取整后去掉空段。"""
    count = max(1, min(workers, height))
    edges = [round(i * height / count) for i in range(count + 1)]
    return [(edges[i], edges[i + 1]) for i in range(count)
            if edges[i + 1] > edges[i]]


def run_bands(height: int, fn: Callable[[int, int], object], pool=None,
              workers: int = 1) -> list:
    """按行区间调用 ``fn(y0, y1)``，返回值的顺序与行顺序一致。

    ``pool`` 为 ``None`` 时全部在当前线程串行执行；线程池的并发度由它自己的
    ``max_workers`` 决定，``workers`` 只决定切几段。
    """
    bands = band_edges(height, workers if pool is not None else 1)
    if pool is None or len(bands) == 1:
        return [fn(y0, y1) for y0, y1 in bands]
    return list(pool.map(lambda band: fn(band[0], band[1]), bands))


def run_tasks(count: int, fn: Callable[[int], object], pool=None) -> list:
    """按序号调用 ``fn(i)``，返回值的顺序与序号一致；用于彼此独立的任务。"""
    if pool is None or count <= 1:
        return [fn(i) for i in range(count)]
    return list(pool.map(fn, range(count)))


def executor(workers: int) -> ThreadPoolExecutor | None:
    """``workers > 1`` 时新建线程池，否则返回 ``None`` 表示串行。

    调用方负责 ``with`` 关闭。单核机器上不创建只会串行执行的线程池。
    """
    return ThreadPoolExecutor(max_workers=workers) if workers > 1 else None


__all__ = ["RESERVED_CORES", "MAX_WORKERS", "workers_for", "band_edges",
           "run_bands", "run_tasks", "executor"]
