import pytest

from app.errors import ToolExecutionError
from app.parser import parse_and_eval


def test_multiply():
    assert parse_and_eval("12 * 8") == 96


def test_add_chain():
    assert parse_and_eval("1 + 2 + 3") == 6


def test_subtract_is_left_associative():
    assert parse_and_eval("10 - 4 - 3") == 3


def test_precedence():
    assert parse_and_eval("2 * 3 + 4") == 10


def test_unary_minus():
    assert parse_and_eval("-3 + 5") == 2


def test_parentheses():
    assert parse_and_eval("2 * (3 + 4)") == 14


def test_division_by_zero():
    with pytest.raises(ToolExecutionError) as exc_info:
        parse_and_eval("1/0")

    assert "division" in exc_info.value.message.lower()


def test_invalid_double_operator():
    with pytest.raises(ToolExecutionError):
        parse_and_eval("1++")


def test_invalid_character():
    with pytest.raises(ToolExecutionError):
        parse_and_eval("1 + a")


def test_empty_expression():
    with pytest.raises(ToolExecutionError):
        parse_and_eval("")