"""指令参数类型转换与校验工具。

不依赖框架的类型注解转换，在指令处理函数内部手动尝试转换，
转换失败时返回友好的中文错误提示。

用法:
    page, err = 转整数(page, "页码", 最小值=1)
    if err:
        event.set_result(MessageEventResult().message(err).use_t2i(False))
        return
"""

from __future__ import annotations


def 转整数(
    raw,
    名称: str = "参数",
    *,
    最小值: int | None = None,
    最大值: int | None = None,
) -> tuple[int | None, str]:
    """尝试将 raw 转为 int，并做范围校验。

    成功返回 (value, "")，失败返回 (None, 友好提示)。
    兼容框架传入 int 或 str 的情况。
    """
    if raw is None:
        return None, f"请输入{名称}。"
    if isinstance(raw, bool):
        return None, f"「{名称}」需要是数字。"
    if isinstance(raw, int):
        val = raw
    else:
        try:
            val = int(str(raw).strip())
        except (ValueError, TypeError):
            return None, f"「{名称}」需要是数字，输入的是「{raw}」。"
    if 最小值 is not None and val < 最小值:
        return None, f"「{名称}」不能小于 {最小值}。"
    if 最大值 is not None and val > 最大值:
        return None, f"「{名称}」不能大于 {最大值}。"
    return val, ""


def 校验非空(raw, 名称: str = "参数") -> tuple[str, str]:
    """去除首尾空格后检查是否为空。

    成功返回 (value, "")，失败返回 ("", 友好提示)。
    """
    val = str(raw or "").strip()
    if not val:
        return "", f"请输入{名称}。"
    return val, ""


def 转整数或None(
    raw,
    名称: str = "参数",
    *,
    最小值: int | None = None,
    最大值: int | None = None,
) -> tuple[int | None, str]:
    """同 转整数，但 raw 为 None 或空字符串时返回 (None, "")（不报错）。

    适用于可选的数字参数（如 /switch 序号 省略时显示帮助）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, ""
    return 转整数(raw, 名称, 最小值=最小值, 最大值=最大值)
