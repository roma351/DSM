from typing import List, Literal
from pydantic import BaseModel

SortDir = Literal["asc", "desc"]


class SortItem(BaseModel):
    field: str
    dir: SortDir = "desc"


SortBy = List[SortItem]
Fields = List[str]
