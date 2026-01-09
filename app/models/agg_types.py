# app/models/agg_types.py
from enum import Enum
from typing import Dict, Optional
from pydantic import BaseModel, model_validator

from app.api.errors import ValidationError
from app.utils.sql_ident import validate_sql_alias, validate_sql_col, validate_sql_func


class AggOp(str, Enum):
    count = "count"
    sum = "sum"
    avg = "avg"
    min = "min"
    max = "max"
    uniq = "uniq"
    custom = "custom"  # driver-specific whitelist


class AggSpec(BaseModel):
    op: AggOp
    field: Optional[str] = None            # count(*) allowed when None
    custom_name: Optional[str] = None      # required for op=custom

    @model_validator(mode="after")
    def _validate(self):
        if self.op in (AggOp.sum, AggOp.avg, AggOp.min, AggOp.max, AggOp.uniq) and not self.field:
            raise ValidationError(f"Aggregate '{self.op}' requires 'field'")

        if self.op == AggOp.custom:
            if not self.custom_name:
                raise ValidationError("Aggregate 'custom' requires 'custom_name'")
            validate_sql_func(self.custom_name)

        if self.field is not None:
            validate_sql_col(self.field)

        return self


Aggregates = Dict[str, AggSpec]
