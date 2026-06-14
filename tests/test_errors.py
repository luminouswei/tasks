from app.errors import (
    AppError,
    ToolExecutionError,
    ToolInputError,
    CODE_TO_STATUS,
)


def test_tool_input_error_has_code_and_status():
    """用户责任(非法参数):HTTP 400。"""
    e = ToolInputError("bad input")
    assert e.code == "TOOL_INPUT_ERROR"
    assert e.http_status == 400
    assert e.message == "bad input"


def test_tool_execution_error_has_code_and_status():
    """工具责任(工具内部崩):HTTP 500。"""
    e = ToolExecutionError("oops")
    assert e.code == "TOOL_EXECUTION_ERROR"
    assert e.http_status == 500
    assert e.message == "oops"


def test_app_error_is_base_with_defaults():
    e = AppError("hi")
    assert e.code == "INTERNAL_ERROR"
    assert e.http_status == 500
    assert e.message == "hi"
    assert isinstance(e, AppError)
    assert issubclass(ToolExecutionError, AppError)
    assert issubclass(ToolInputError, AppError)


def test_tool_execution_error_inherits_app_error():
    assert issubclass(ToolExecutionError, AppError)


def test_tool_input_error_inherits_app_error():
    assert issubclass(ToolInputError, AppError)


def test_code_to_status_mapping():
    """完整 error.code -> HTTP 状态码映射。
    责任边界:
    - TOOL_NOT_FOUND: 404
    - TOOL_INPUT_ERROR: 400 (用户错,改请求重发)
    - TOOL_EXECUTION_ERROR: 500 (工具崩了,要告警)
    - INTERNAL_ERROR: 500 (系统崩了)
    - TRACE_PERSIST_FAILED: 500 (业务 OK 但 trace 落库失败)
    - RUN_NOT_FOUND: 404
    - INVALID_REQUEST: 422
    """
    assert CODE_TO_STATUS["TOOL_NOT_FOUND"] == 404
    assert CODE_TO_STATUS["TOOL_INPUT_ERROR"] == 400
    assert CODE_TO_STATUS["TOOL_EXECUTION_ERROR"] == 500
    assert CODE_TO_STATUS["RUN_NOT_FOUND"] == 404
    assert CODE_TO_STATUS["INVALID_REQUEST"] == 422
    assert CODE_TO_STATUS["INTERNAL_ERROR"] == 500
    assert CODE_TO_STATUS["TRACE_PERSIST_FAILED"] == 500
