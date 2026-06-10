import pytest

import app.tools.calculator  # noqa: F401
from app.errors import ToolExecutionError
from app.registry import get_tool


def test_calculator_tool_runs_expression():
    spec = get_tool("calculator")

    assert spec.name == "calculator"
    assert spec.run("12 * 8") == 96


def test_calculator_tool_propagates_tool_execution_error():
    spec = get_tool("calculator")

    with pytest.raises(ToolExecutionError):
        spec.run("1/0")