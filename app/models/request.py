import datetime
from typing import Dict, Optional, Union, Literal, List

from pydantic import BaseModel, Field, model_validator

from app.models.filter_utils import (
    normalize_entity,
    normalize_filters,
    apply_history_window,
    add_filter_to_all_groups,
)
from app.models.filter_types import EntityKey, FilterClause, FilterGroup, FilterGroups


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
    fields: List[str] = None
    filters: Union[FilterGroups, FilterGroup, None] = Field(default_factory=dict)
    limit: int = 100
    offset: int = 0
    sort: Literal["asc", "desc"] = "asc"
    at_date: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self):
        self.filters = normalize_filters(self.filters)

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
    fields: List[str] = None
    filters: Union[FilterGroups, FilterGroup, None] = Field(default_factory=dict)
    entity_id: Optional[str] = None
    limit: int = 100
    offset: int = 0
    sort: Literal["asc", "desc"] = "asc"
    from_date: Optional[str] = None
    to_date: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self):
        self.filters = normalize_filters(self.filters)

        if self.entity_id is not None:
            self.filters = add_filter_to_all_groups(self.filters, "entity_id", FilterClause(op="=", value=self.entity_id))

        self.filters = apply_history_window(self.filters, self.from_date, self.to_date)

        if self.limit < 1:
            self.limit = 1
        if self.limit > 1000:
            self.limit = 1000
        if self.offset < 0:
            self.offset = 0

        return self
