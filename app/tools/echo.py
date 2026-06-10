from app.registry import register_tool


@register_tool(name="echo", description="回显输入")
def run(message: str) -> str:
    return message