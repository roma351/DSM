from typing import Any, Dict
import yaml

from app.bootstrap.envsubst import envsubst


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return envsubst(data)
