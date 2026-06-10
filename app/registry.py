from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolSpec:
    name: str
    description: str
    run: Callable[[str], Any]

_registry: dict[str, ToolSpec] = {}

def register_tool(name: str,*, description: str):
    def decorator(func: Callable[[str], Any]):
        _registry[name] = ToolSpec(name=name, description=description, run=func)
        return func
    return decorator

def get_tool(name: str) -> ToolSpec:
    if name not in _registry:
        raise KeyError(f"Tool '{name}' is not registered.")
    return _registry[name]

def registered_names() -> list[str]:
    return sorted(_registry.keys())