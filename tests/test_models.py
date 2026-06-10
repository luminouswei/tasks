import pytest
from pydantic import ValidationError

from app.models import RunRequest

def test_run_request_valid():
    req = RunRequest(message="hi", tool="echo")

    assert req.message == "hi"
    assert req.tool == "echo"

def test_run_request_missing_message():
    with pytest.raises(ValidationError):
        RunRequest(tool="echo")

def test_run_request_empty_message_after_trim():
    with pytest.raises(ValidationError):
        RunRequest(message="   ", tool="echo")

def test_run_request_rejects_tool_with_space():
    with pytest.raises(ValidationError):
        RunRequest(message="hi", tool="invalid tool")

def test_run_request_rejects_too_long_message():
    long_message = "a" * 2001
    with pytest.raises(ValidationError):
        RunRequest(message=long_message, tool="echo")

def test_run_request_rejects_uppercase_tool():
    with pytest.raises(ValidationError):
        RunRequest(message="hi", tool="Weather")
