from typing import Dict, List, Union, Literal

from pydantic import BaseModel

EntityKey = Dict[str, int | str]

FilterValue = int | str
FilterOp = Literal["=", "!=", "<", "<=", ">", ">=", "in"]


class FilterClause(BaseModel):
    op: FilterOp
    value: Union[FilterValue, List[FilterValue]]


FilterExpr = Union[FilterValue, List[FilterValue], FilterClause]
FilterGroup = Dict[str, FilterExpr]
FilterGroups = List[FilterGroup]
