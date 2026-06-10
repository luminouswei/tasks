class AppError(Exception):
    """Base class for all application errors."""
    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

class ToolExecutionError(AppError):
    code = "TOOL_EXECUTION_ERROR"
    http_status = 400

CODE_TO_STATUS = {
    "TOOL_NOT_FOUND": 404,
    "TOOL_EXECUTION_ERROR": 400,
    "TRACE_NOT_FOUND": 404,
    "INVALID_REQUEST": 422,
    "INTERNAL_ERROR": 500,
}