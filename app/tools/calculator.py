from app.parser import parse_and_eval
from app.registry import register_tool


@register_tool(name="calculator", description="四则运算")
def run(message: str) -> float:
    return parse_and_eval(message)