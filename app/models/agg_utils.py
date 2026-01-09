from typing import Dict

from app.api.errors import ValidationError
from app.models.agg_types import Aggregates, AggSpec


def normalize_aggs(aggs: Aggregates) -> Aggregates:
    out: Dict[str, AggSpec] = {}
    for alias, spec in aggs.items():
        if not alias or not isinstance(alias, str):
            raise ValidationError("Aggregate alias must be a non-empty string")

        # if spec.op == "count" and spec.distinct:
        #     # alias for uniq
        #     out[alias] = AggSpec(op="uniq", field=spec.field)
        #     continue

        if spec.op == "uniq":
            if not spec.field:
                raise ValidationError(f"uniq requires 'field' (alias '{alias}')")
            out[alias] = AggSpec(op="uniq", field=spec.field)
            continue

        if spec.op in ("sum", "avg", "min", "max"):
            if not spec.field:
                raise ValidationError(f"{spec.op} requires 'field' (alias '{alias}')")
            # if spec.distinct:
            #     raise ValidationError(f"{spec.op} does not support distinct (alias '{alias}')")
            out[alias] = AggSpec(op=spec.op, field=spec.field)
            continue

        if spec.op == "count":
            # count(*) allowed if field is None
            out[alias] = AggSpec(op="count", field=spec.field)
            continue

        # if spec.op == "raw":
        #     if not spec.expr:
        #         raise ValidationError(f"raw requires 'expr' (alias '{alias}')")
        #     out[alias] = AggSpec(op="raw", expr=spec.expr)
        #     continue

        raise ValidationError(f"Unsupported aggregate op: {spec.op}")

    return out
