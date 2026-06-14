class AppError(Exception):
    """Base class for all application errors."""
    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ToolInputError(AppError):
    """工具检测到用户输入不合法(用户责任),如除零、非法参数。

    HTTP 400:用户需要改请求重发。不属于工具 bug,不需要 PagerDuty 告警。
    """
    code = "TOOL_INPUT_ERROR"
    http_status = 400


class ToolExecutionError(AppError):
    """工具内部执行异常(工具责任),如第三方 API 超时、数据库连接失败、工具代码 bug。

    HTTP 500:工具的责任,需要告警和修复。跟 ToolInputError 区分开。
    """
    code = "TOOL_EXECUTION_ERROR"
    http_status = 500


CODE_TO_STATUS = {
    "TOOL_NOT_FOUND": 404,
    "TOOL_INPUT_ERROR": 400,
    "TOOL_EXECUTION_ERROR": 500,
    "RUN_NOT_FOUND": 404,
    "INVALID_REQUEST": 422,
    "INTERNAL_ERROR": 500,
    "TRACE_PERSIST_FAILED": 500,
}