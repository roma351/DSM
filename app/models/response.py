import datetime

from pydantic import BaseModel, Field, field_validator
from typing import Optional

# ============================================================================
# API RESPONSE MODELS
# ============================================================================


class SaveResponse(BaseModel):
    success: bool
    changed: bool


class QueryResponse(BaseModel):
    results: list[dict]
    count: int
    total: int


class HistoryResponse(BaseModel):
    history: list[dict]
    count: int
    total: int
