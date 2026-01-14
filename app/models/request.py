import datetime
from typing import Dict, Optional, Union, Literal, List

from pydantic import BaseModel, Field, model_validator

from app.api.errors import ValidationError
from app.models.agg_types import Aggregates, AggSpec, AggOp
from app.models.agg_utils import normalize_aggs
from app.models.filter_utils import (
    normalize_entity,
    normalize_filters,
    apply_history_window,
    add_filter_to_all_groups,
)
from app.models.filter_types import EntityKey, FilterClause, FilterGroup, FilterGroups
from app.models.query_types import Fields, SortBy, SortItem
from app.models.query_utils import normalize_sort_by


class SaveRequest(BaseModel):
    table: str
    entity: Optional[EntityKey] = None
    entity_id: Optional[str] = None
    data: Dict
    now: datetime.datetime

    @model_validator(mode="after")
    def _validate(self):
        self.entity = normalize_entity(self.entity, self.entity_id)
        return self


class GetRequest(BaseModel):
    table: str
    entity: Optional[EntityKey] = None
    entity_id: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self):
        self.entity = normalize_entity(self.entity, self.entity_id)
        return self


class QueryRequest(BaseModel):
    table: str
    fields: Fields = None
    filters: Union[FilterGroups, FilterGroup, None] = None
    sort_by: Union[SortBy, SortItem] = None
    limit: int = 100
    offset: int = 0

    at_date: Optional[str] = None
    sort: Literal["desc", "asc"] = "desc"

    @model_validator(mode="after")
    def _validate(self):
        self.filters = normalize_filters(self.filters)
        self.sort_by = normalize_sort_by(self.sort_by)

        if not self.sort_by:
            self.sort_by = [SortItem(field="created_at", dir=self.sort)]

        if self.limit < 1:
            self.limit = 1
        if self.limit > 1000:
            self.limit = 1000
        if self.offset < 0:
            self.offset = 0

        if self.at_date:
            self.filters = add_filter_to_all_groups(self.filters, "created_at", FilterClause(op="<=", value=self.at_date))
            self.filters = add_filter_to_all_groups(self.filters, "updated_at", FilterClause(op=">=", value=self.at_date))

        return self


class HistoryRequest(BaseModel):
    table: str
    fields: Fields = None
    filters: Union[FilterGroups, FilterGroup, None] = None
    sort_by: Union[SortBy, SortItem] = None
    limit: int = 100
    offset: int = 0

    entity_id: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    sort: Literal["desc", "asc"] = "desc"

    @model_validator(mode="after")
    def _validate(self):
        self.filters = normalize_filters(self.filters)
        self.sort_by = normalize_sort_by(self.sort_by)

        if self.entity_id is not None:
            self.filters = add_filter_to_all_groups(self.filters, "entity_id", FilterClause(op="=", value=self.entity_id))

        if not self.sort_by:
            self.sort_by = [SortItem(field="archived_at", dir=self.sort)]

        self.filters = apply_history_window(self.filters, self.from_date, self.to_date)

        if self.limit < 1:
            self.limit = 1
        if self.limit > 1000:
            self.limit = 1000
        if self.offset < 0:
            self.offset = 0

        return self


class GroupRequest(BaseModel):
    table: str
    fields: Fields = None
    filters: Union[FilterGroups, FilterGroup, None] = None
    sort_by: Union[SortBy, SortItem] = None
    group_by: List[str] = Field(default_factory=list)
    aggs: Aggregates = Field(default_factory=dict)
    having: Union[FilterGroups, FilterGroup] = None
    limit: int = 100
    offset: int = 0

    is_history: bool = False

    sort: Literal["desc", "asc"] = "desc"

    @model_validator(mode="after")
    def _validate(self):
        self.filters = normalize_filters(self.filters)
        self.having = normalize_filters(self.having)
        self.aggs = normalize_aggs(self.aggs)
        self.sort_by = normalize_sort_by(self.sort_by)

        if not self.group_by:
            raise ValidationError("group_by is required")

        if not self.aggs:
            self.aggs = {"count": AggSpec(op=AggOp.count)}

        if not self.sort_by or len(self.sort_by) == 0:
            if self.aggs:
                first_alias = next(iter(self.aggs.keys()))
                self.sort_by = [SortItem(field=first_alias, dir=self.sort if self.sort else "desc")]
            elif self.group_by:
                self.sort_by = [SortItem(field=self.group_by[0], dir=self.sort if self.sort else "asc")]
            else:
                self.sort_by = []

        if self.limit < 1:
            self.limit = 1
        if self.limit > 1000:
            self.limit = 1000
        if self.offset < 0:
            self.offset = 0

        return self
