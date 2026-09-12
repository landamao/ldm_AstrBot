"""env 示例模板回归：取消行首注释后，行尾说明不得被解析进变量值。"""

import re
from io import StringIO

from dotenv import dotenv_values

from astrbot.utils.env_file import _ENV_EXAMPLE_SECTIONS, _render_env_example

_VARIABLE_LINE = re.compile(r"^# [A-Za-z_][A-Za-z0-9_]*=")


def _uncommented_variable_lines(rendered: str) -> list[str]:
    return [
        line[2:]
        for line in rendered.splitlines()
        if _VARIABLE_LINE.match(line)
    ]


def test_uncommented_lines_parse_cleanly():
    rendered = _render_env_example()
    var_lines = _uncommented_variable_lines(rendered)

    total = sum(len(entries) for _, entries in _ENV_EXAMPLE_SECTIONS)
    assert len(var_lines) == total

    values = dotenv_values(stream=StringIO("\n".join(var_lines)))
    assert set(values) == {name.split("=", 1)[0] for name, _ in _iter_entries()}

    for name, _ in _iter_entries():
        key, expected = name.split("=", 1)
        # 解析值必须恰好是占位符，说明文字不得混入
        assert values[key] == expected, (key, values[key], expected)


def _iter_entries():
    for _, entries in _ENV_EXAMPLE_SECTIONS:
        yield from entries


def test_filled_value_with_inline_comment():
    """用户替换占位值并取消注释后，行内说明应被 dotenv 忽略。"""
    rendered = _render_env_example()
    target = next(
        line
        for line in rendered.splitlines()
        if line.startswith("# LDMBOT_DASHBOARD_PORT=")
    )
    enabled = target[2:].replace("<端口>", "6186")

    values = dotenv_values(stream=StringIO(enabled))
    assert values["LDMBOT_DASHBOARD_PORT"] == "6186"
