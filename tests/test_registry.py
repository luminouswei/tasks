import pytest

from app.registry import get_tool, registered_names


def test_echo_tool_is_registered():
    import app.tools.echo  

    spec = get_tool("echo")

    assert spec.name == "echo"
    assert spec.description == "回显输入"
    assert spec.run("hi") == "hi"


def test_get_tool_missing_raises_key_error():
    with pytest.raises(KeyError):
        get_tool("nope")


def test_registered_names_contains_echo():
    import app.tools.echo  # noqa: F401

    assert "echo" in registered_names()