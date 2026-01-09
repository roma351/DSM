from typing import Optional, Union

from app.api.errors import ValidationError
from app.models.filter_types import EntityKey, FilterClause, FilterGroup, FilterGroups


def normalize_entity(entity: Optional[EntityKey], entity_id: Optional[str]) -> EntityKey:
    if entity is None:
        if entity_id is None:
            raise ValidationError("Either 'entity' or 'entity_id' must be provided")
        return {"entity_id": entity_id}

    if not isinstance(entity, dict) or not entity:
        raise ValidationError("'entity' must be a non-empty dict")

    for k, v in entity.items():
        if not isinstance(v, (int, str)):
            raise ValidationError(f"Invalid entity value for '{k}', expected int|str")

    return entity


def normalize_filters(filters: Union[FilterGroups, FilterGroup, None]) -> FilterGroups:
    if filters is None:
        return []
    elif isinstance(filters, dict):
        groups = [filters]
    else:
        groups = list(filters)

    norm: FilterGroups = []
    for g in groups:
        if not isinstance(g, dict) or not g:
            raise ValidationError("Each filters group must be a non-empty dict")

        ng: FilterGroup = {}
        for name, expr in g.items():
            if isinstance(expr, FilterClause):
                ng[name] = expr
            elif isinstance(expr, (str, int)):
                ng[name] = FilterClause(op="=", value=expr)
            elif isinstance(expr, list):
                ng[name] = FilterClause(op="in", value=expr)
            elif isinstance(expr, dict):
                op = expr.get("op")
                val = expr.get("value")
                if op is None or val is None:
                    raise ValidationError(f"Invalid filter for '{name}': expected {{op, value}}")
                ng[name] = FilterClause(op=op, value=val)
            else:
                raise ValidationError(f"Invalid filter value for '{name}'")
        norm.append(ng)

    return norm


def add_filter_to_all_groups(groups: FilterGroups, name: str, clause: FilterClause) -> FilterGroups:
    if not groups:
        return [{name: clause}]
    for g in groups:
        g[name] = clause
    return groups


def apply_history_window(groups: FilterGroups, from_date: Optional[str], to_date: Optional[str]) -> FilterGroups:
    if from_date and to_date:
        add_filter_to_all_groups(groups, "created_at", FilterClause(op="<=", value=to_date))
        add_filter_to_all_groups(groups, "archived_at", FilterClause(op=">=", value=from_date))
        return groups

    if from_date:
        add_filter_to_all_groups(groups, "archived_at", FilterClause(op=">=", value=from_date))
        return groups

    if to_date:
        add_filter_to_all_groups(groups, "created_at", FilterClause(op="<=", value=to_date))
        return groups

    return groups
