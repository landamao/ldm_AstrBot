"""分段回复线性模式的延迟钳制（最短/最长间隔）。

回归场景：线性模式延迟 = linear_base + 字数 × linear_factor，原先没有上限，
长段（如整块代码、超长不可分段文本）会算出几十秒的等待。对数模式内置 8 秒
硬顶而线性没有。修复：线性结果先抬 linear_min_delay 下限再钳 linear_max_delay
上限，两者均为 0 时不生效，行为退回原始线性值。

运行：.venv/Scripts/python.exe -m pytest tests/test_segmented_reply_delay_clamp.py -q
"""

import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.utils.segmented_reply import (  # noqa: E402
    calculate_segment_delay,
    resolve_segmented_reply_config,
)


def test_linear_clamp_max_default():
    """默认线性最长 4 秒：400 字按 base 0.1 + 0.08/字 本应 32 秒，须钳到 4。"""
    delay = calculate_segment_delay(
        "字" * 400, method="linear", linear_base=0.1, linear_factor=0.08
    )
    assert delay == 4.0


def test_linear_clamp_max_zero_means_unlimited():
    """linear_max_delay=0 表示不设上限，恢复原始线性值。"""
    delay = calculate_segment_delay(
        "字" * 400,
        method="linear",
        linear_base=0.1,
        linear_factor=0.08,
        linear_max_delay=0.0,
    )
    assert abs(delay - (0.1 + 400 * 0.08)) < 1e-9


def test_linear_clamp_min():
    """linear_min_delay 把过短的延迟抬到下限。"""
    delay = calculate_segment_delay(
        "hi", method="linear", linear_base=0.1, linear_factor=0.08, linear_min_delay=1.0
    )
    assert delay == 1.0


def test_linear_clamp_min_zero_means_no_floor():
    """linear_min_delay=0 时不抬下限，短段保持原值。"""
    delay = calculate_segment_delay(
        "hi", method="linear", linear_base=0.1, linear_factor=0.08
    )
    assert abs(delay - 0.26) < 1e-9


def test_linear_clamp_max_priority_over_min():
    """上限优先于下限：钳到上限后不再被下限抬高。"""
    delay = calculate_segment_delay(
        "字" * 400,
        method="linear",
        linear_base=0.1,
        linear_factor=0.08,
        linear_min_delay=10.0,
        linear_max_delay=4.0,
    )
    assert delay == 4.0


def test_other_methods_unaffected():
    """fixed / random / log 不受线性钳制参数影响。"""
    assert calculate_segment_delay("abc", method="fixed", fixed_delay=2.0) == 2.0
    r = calculate_segment_delay("abc", method="random", interval=(1.0, 2.0))
    assert 1.0 <= r <= 2.0
    log_d = calculate_segment_delay(
        "字" * 400, method="log", log_offset=0.5, log_factor=0.8
    )
    assert log_d <= 8.0


def test_resolve_defaults_contain_clamp_keys():
    """resolve 输出包含新键，默认值 0.0 / 4.0。"""
    out = resolve_segmented_reply_config({})
    assert out["linear_min_delay"] == 0.0
    assert out["linear_max_delay"] == 4.0


def test_resolve_pro_keeps_user_clamp_values():
    """专业模式保留用户自定的钳制值。"""
    out = resolve_segmented_reply_config(
        {
            "config_mode": "pro",
            "linear_min_delay": 0.5,
            "linear_max_delay": 12.0,
        }
    )
    assert out["linear_min_delay"] == 0.5
    assert out["linear_max_delay"] == 12.0


def test_resolve_simple_ignores_leftover_persisted_values():
    """简易模式不展示钳制项：落盘残留值（如 pro 切回 simple 的脏值）必须被忽略，走当前默认。"""
    out = resolve_segmented_reply_config(
        {
            "config_mode": "simple",
            "linear_min_delay": 99.0,
            "linear_max_delay": 99.0,
        }
    )
    assert out["linear_min_delay"] == 0.0
    assert out["linear_max_delay"] == 4.0


def test_resolve_advanced_ignores_leftover_persisted_values():
    """进阶模式同样不展示钳制项：落盘残留值被忽略。"""
    out = resolve_segmented_reply_config(
        {
            "config_mode": "advanced",
            "linear_min_delay": 99.0,
            "linear_max_delay": 99.0,
        }
    )
    assert out["linear_min_delay"] == 0.0
    assert out["linear_max_delay"] == 4.0
