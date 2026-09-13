"""CommandFilter 参数解析测试：类型注解优先于默认值运行时类型。

对齐上游 PR #9866 (fix: respect type annotations when parsing command parameters)。
"""
from types import SimpleNamespace

import pytest

from astrbot.core.star.filter.command import CommandFilter


async def 延迟注解处理器(self, event, machine: str, retries: int = 1) -> None:
    pass


def test_延迟注解按类型解析():
    command_filter = CommandFilter(
        "cmd",
        handler_md=SimpleNamespace(handler=延迟注解处理器),
    )

    assert command_filter.handler_params == {"machine": str, "retries": int}
    assert command_filter.handler_param_defaults == {"retries": 1}
    assert command_filter.validate_and_convert_params(
        ["server-1", "2"],
        command_filter.handler_params,
    ) == {"machine": "server-1", "retries": 2}


def test_延迟注解缺必填参数报错():
    command_filter = CommandFilter(
        "cmd",
        handler_md=SimpleNamespace(handler=延迟注解处理器),
    )

    with pytest.raises(ValueError, match="必要参数缺失"):
        command_filter.validate_and_convert_params([], command_filter.handler_params)


async def 可选str处理器(self, event, text: str = None) -> None:
    pass


async def 可选联合处理器(self, event, text: str | None = None) -> None:
    pass


async def 必填bool处理器(self, event, flag: bool) -> None:
    pass


async def 带默认值str处理器(self, event, text: str = "fallback") -> None:
    pass


async def 无注解处理器(self, event, arg) -> None:
    pass


@pytest.mark.parametrize(
    "handler",
    [可选str处理器, 可选联合处理器],
)
def test_str注解保持数字字符串(handler):
    """注解为 str 时，数字字符串必须保持 str 类型。"""
    command_filter = CommandFilter("echo", handler_md=SimpleNamespace(handler=handler))

    result = command_filter.validate_and_convert_params(
        ["123"],
        command_filter.handler_params,
    )

    assert result == {"text": "123"}
    assert isinstance(result["text"], str)


def test_纯注解bool解析():
    """纯注解 bool 应解析 true/false，而不是走 bool() 真值判断。"""
    command_filter = CommandFilter(
        "flag",
        handler_md=SimpleNamespace(handler=必填bool处理器),
    )

    assert command_filter.validate_and_convert_params(
        ["false"],
        command_filter.handler_params,
    ) == {"flag": False}


def test_缺参时使用默认值():
    command_filter = CommandFilter(
        "echo",
        handler_md=SimpleNamespace(handler=带默认值str处理器),
    )

    assert command_filter.validate_and_convert_params(
        [],
        command_filter.handler_params,
    ) == {"text": "fallback"}


def test_无注解保持数字转int启发式():
    """无注解参数保持旧的数字转 int 启发式。"""
    command_filter = CommandFilter(
        "t",
        handler_md=SimpleNamespace(handler=无注解处理器),
    )

    assert command_filter.validate_and_convert_params(
        ["123"],
        command_filter.handler_params,
    ) == {"arg": 123}
