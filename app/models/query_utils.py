# app/models/query_utils.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from app.api.errors import ValidationError
from app.models.query_types import SortBy, SortItem, SortDir
from app.utils.sql_ident import validate_sql_col


def normalize_sort_by(
    sort_by: Any
) -> SortBy:
    if sort_by is None:
        return []

    # already one SortItem
    if isinstance(sort_by, SortItem):
        out = [sort_by]
        return _validate_sort_by(out)

    # dict -> one SortItem
    if isinstance(sort_by, dict):
        out = [_dict_to_sort_item(sort_by)]
        return _validate_sort_by(out)

    # list -> SortBy
    if isinstance(sort_by, list):
        if len(sort_by) == 0:
            return []

        # list[SortItem]
        if all(isinstance(x, SortItem) for x in sort_by):
            out = list(sort_by)
            return _validate_sort_by(out)

        # list[dict]
        if all(isinstance(x, dict) for x in sort_by):
            out2 = [_dict_to_sort_item(x) for x in sort_by]
            out2 = [x for x in out2 if x is not None]
            return _validate_sort_by(out2)

        raise ValidationError("Invalid sort_by: expected list[SortItem] or list[{field, dir}]")

    raise ValidationError("Invalid sort_by: expected SortItem, SortBy, {field, dir}, or list[...]")


def _dict_to_sort_item(d: Dict[str, Any]) -> SortItem:
    field = d.get("field")
    dir2 = d.get("dir")

    if field is None or dir2 is None:
        raise ValidationError("Invalid sort_by item: expected {field, dir}")

    if not isinstance(field, str) or not field.strip():
        raise ValidationError("Invalid sort_by.field: must be a non-empty string")

    if dir2 not in ("asc", "desc"):
        raise ValidationError("Invalid sort_by.dir: must be 'asc' or 'desc'")

    return SortItem(field=field, dir=dir2)


def _validate_sort_by(items: List[SortItem]) -> SortBy:
    if not items:
        return []

    out: List[SortItem] = []
    for it in items:
        # normalize potential whitespace
        field = it.field.strip() if isinstance(it.field, str) else it.field
        dir2 = it.dir

        if dir2 not in ("asc", "desc"):
            raise ValidationError("Invalid sort_by.dir: must be 'asc' or 'desc'")

        if not isinstance(field, str) or not field:
            raise ValidationError("Invalid sort_by.field: must be a non-empty string")

        validate_sql_col(field)

        out.append(SortItem(field=field, dir=dir2))

    return out


# -------------------------------------------------------------------
# Optional helpers (nice to keep next to normalize_sort_by)

def default_records_sort_by(
    *,
    is_history: bool,
    prefer: str = "created_at",
    history_prefer: str = "created_at",
    dir2: SortDir = "desc",
) -> SortBy:
    """
    Keeps request.py clean.
    You can override defaults in request validators.
    """
    field = history_prefer if is_history else prefer
    if dir2 not in ("asc", "desc"):
        dir2 = "desc"
    return [SortItem(field=field, dir=dir2)]
