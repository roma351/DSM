from datetime import datetime
from abc import ABC, abstractmethod

from typing import Optional, Callable, Tuple, List, Dict

from app.db.table_schema import TableSchema
from app.models.agg_types import Aggregates
from app.models.filter_types import FilterGroups
from app.models.query_types import SortBy

# ============================================================================
# DATABASE DRIVER
# ============================================================================

EntityKey = Dict[str, int | str]


class DatabaseDriver(ABC):
    """Abstract base class for database drivers"""

    def __init__(self, config: dict):
        self.pool = None

    @abstractmethod
    async def get_current(
            self,
            schema: TableSchema,
            entity: EntityKey
    ) -> Optional[dict]:
        """Close connection pool"""
        pass

    @abstractmethod
    async def save_scd(
        self,
        schema: TableSchema,
        entity: EntityKey,
        data: dict,
        now: datetime,
        save_history_func: Callable = None
    ) -> bool:
        """Close connection pool"""
        pass

    @abstractmethod
    async def save_current(
            self,
            schema: TableSchema,
            entity: EntityKey,
            data: dict,
            now: datetime
    ) -> bool:
        """Close connection pool"""
        pass

    @abstractmethod
    async def append_history(
            self,
            schema: TableSchema,
            entity: EntityKey,
            data: dict,
            now: datetime
    ) -> bool:
        """Close connection pool"""
        pass

    @abstractmethod
    async def save_history(
            self,
            conn,
            current: dict,
            schema: TableSchema,
            now: datetime
    ) -> None:
        """Close connection pool"""
        pass

    @abstractmethod
    async def get_records(
        self,
        schema: TableSchema,
        fields: list[str],
        filters: FilterGroups,
        is_history: bool,
        limit: int = 100,
        offset: int = 0,
        sort_by: SortBy = None,
    ) -> Tuple[List[dict], int]:
        """Close connection pool"""
        pass

    @abstractmethod
    async def get_group(
            self,
            schema: TableSchema,
            is_history: bool,
            filters: FilterGroups,
            group_by: List[str],
            aggs: Aggregates,
            having: FilterGroups,
            limit: int = 100,
            offset: int = 0,
            sort_by: SortBy = None,
    ) -> Tuple[List[dict], int]:
        """Close connection pool"""
        pass

    @abstractmethod
    async def create_table(
            self,
            schema: TableSchema,
            is_history: bool = False
    ):
        """Close connection pool"""
        pass

    @abstractmethod
    async def verify_indexes(
            self,
            schema: TableSchema,
            is_history: bool = False
    ):
        """Close connection pool"""
        pass

    @abstractmethod
    async def connect(self):
        """Close connection pool"""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection pool"""
        pass
