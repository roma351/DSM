import importlib
from typing import Any


def import_from_path(path: str) -> Any:
    if ":" not in path:
        raise ValueError(f"Invalid import path '{path}', expected 'module:attr'")
    mod, attr = path.split(":", 1)
    m = importlib.import_module(mod)
    try:
        return getattr(m, attr)
    except AttributeError as e:
        raise ValueError(f"Attribute '{attr}' not found in module '{mod}'") from e
