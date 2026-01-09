# app/utils/sql_ident.py
import re

_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FUNC_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # same rule


def validate_sql_alias(alias: str) -> str:
    if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
        raise ValueError(f"Invalid SQL alias '{alias}'. Allowed: [A-Za-z_][A-Za-z0-9_]*")
    return alias


def validate_sql_col(name: str) -> str:
    if not isinstance(name, str) or not _COL_RE.match(name):
        raise ValueError(f"Invalid SQL column '{name}'. Allowed: [A-Za-z_][A-Za-z0-9_]*")
    return name


def validate_sql_func(name: str) -> str:
    if not isinstance(name, str) or not _FUNC_RE.match(name):
        raise ValueError(f"Invalid SQL function name '{name}'. Allowed: [A-Za-z_][A-Za-z0-9_]*")
    return name


def validate_output_cols(cols: list[str], allowed: set[str], what: str) -> None:
    for c in cols:
        validate_sql_col(c)
        if c not in allowed:
            raise ValueError(f"Unknown {what} field '{c}'")
