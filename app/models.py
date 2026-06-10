import re

from pydantic import BaseModel, field_validator

class RunRequest(BaseModel):
    message: str
    tool: str

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        if len(value) > 2000:
            raise ValueError("消息长度不能超过2000字符")
        return value
    
    @field_validator("tool")
    @classmethod
    def validate_tool(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("工具名称不能为空")
        if len(value) > 64:
            raise ValueError("工具名称长度不能超过64字符")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("工具名称只能包含小写字母、数字和下划线，且必须以小写字母开头")
        return value