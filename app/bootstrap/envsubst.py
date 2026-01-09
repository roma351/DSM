import os
import re
from typing import Any

_pat = re.compile(r"\$\{([A-Z0-9_]+)\}")


def envsubst_str(s: str) -> str:
    def _rep(m):
        key = m.group(1)
        return os.getenv(key, "")
    return _pat.sub(_rep, s)


def envsubst(obj: Any) -> Any:
    if isinstance(obj, str):
        return envsubst_str(obj)
    if isinstance(obj, list):
        return [envsubst(x) for x in obj]
    if isinstance(obj, dict):
        return {k: envsubst(v) for k, v in obj.items()}
    return obj
