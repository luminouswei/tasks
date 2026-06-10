from app.errors import AppError, ToolExecutionError, CODE_TO_STATUS


def test_tool_execution_has_code_and_status():
    e = ToolExecutionError("oops")
    assert e.code == "TOOL_EXECUTION_ERROR"
    assert e.http_status == 400 
    assert e.message == "oops"

def test_app_error_is_base_with_defaults():
    e = AppError("hi")
    assert e.code == "INTERNAL_ERROR"
    assert e.http_status == 500
    assert e.message == "hi"
    assert isinstance(e, AppError)
    assert issubclass(ToolExecutionError, AppError)

def test_tool_execction_error_inherits_app_error():
    assert issubclass(ToolExecutionError, AppError)

def test_code_to_status_mapping():
    assert CODE_TO_STATUS["TOOL_NOT_FOUND"] == 404
    assert CODE_TO_STATUS["TOOL_EXECUTION_ERROR"] == 400
    assert CODE_TO_STATUS["TRACE_NOT_FOUND"] == 404
    assert CODE_TO_STATUS["INVALID_REQUEST"] == 422
    assert CODE_TO_STATUS["INTERNAL_ERROR"] == 500