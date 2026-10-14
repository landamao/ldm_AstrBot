"""数值库线程池的进程级默认值。

numpy / faiss 这类库在**首次导入**时会按 CPU 核数创建 OpenMP / OpenBLAS 线程池，
每个线程预留一大块私有内存。16 核机器上实测：

- ``import numpy``   提交约 490 MB（单线程时约 9 MB）
- ``import faiss``   提交约 2.4 GB（单线程时约 17 MB）

这些内存不计入常驻工作集，但会占满提交限额、推高页面文件占用并触发系统内存压缩。
机器人的向量检索与相似度计算都是小规模运算，多线程收益接近于零，
因此这里把默认线程数压到 1。

用户可在 ``.env`` 中显式设置 ``OMP_NUM_THREADS`` / ``OPENBLAS_NUM_THREADS`` /
``MKL_NUM_THREADS`` 覆盖本默认值（本函数用 setdefault，不会覆盖已有取值）。
"""

import os

_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)


def apply_thread_env_defaults() -> None:
    """为数值库线程池设定保守默认值，不覆盖用户显式设置。

    必须在任何 numpy / faiss 导入之前调用：线程池参数在库初始化时读取一次，
    之后再改环境变量不再生效。
    """
    for name in _THREAD_ENV_VARS:
        os.environ.setdefault(name, "1")
