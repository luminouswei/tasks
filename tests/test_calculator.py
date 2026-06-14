import pytest

import app.tools.calculator  # noqa: F401
from app.errors import ToolInputError
from app.registry import get_tool


def test_calculator_tool_runs_expression():
    spec = get_tool("calculator")

    assert spec.name == "calculator"
    assert spec.run("12 * 8") == 96


def test_calculator_tool_propagates_tool_input_error():
    """除零是用户责任(用户给了 0 作除数),走 ToolInputError(HTTP 400)。
    跟 ToolExecutionError(HTTP 500,工具责任)区分开。"""
    spec = get_tool("calculator")

    with pytest.raises(ToolInputError):
        spec.run("1/0")