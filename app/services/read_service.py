from typing import Optional, Tuple, List, Dict

from app.api.errors import ValidationError
from app.db.table_binding import TableBinding
from app.db.table_schema import TableSchema
from app.models.agg_types import Aggregates
from app.models.query_types import SortBy
from app.state import state
from app.db.pools import Pools
from app.utils.entity import normalize_entity, EntityDict
from app.models.filter_types import FilterGroups

# ============================================================================
# READ SERVICE
# ============================================================================


class ReadService:
    @staticmethod
    async def get_current(
        table: str,
        entity: EntityDict,
    ) -> Optional[dict]:
        table_binding: TableBinding = state.TABLES_CONFIG.get(table)
        if table_binding is None:
            raise ValidationError("table not found")

        schema: TableSchema = table_binding.schema

        if not table_binding.has_current():
            return None

        entity_key = normalize_entity(schema, entity)

        pool = Pools.get_pool(table_binding.database)
        return await pool.get_current(schema, entity_key)

    @staticmethod
    async def query(
        table: str,
        fields: list[str],
        filters: FilterGroups,
        limit: int = 100,
        offset: int = 0,
        sort: SortBy = None
    ) -> Tuple[List[dict], int]:
        table_binding: TableBinding = state.TABLES_CONFIG.get(table)
        if table_binding is None:
            raise ValidationError("table not found")

        schema: TableSchema = table_binding.schema

        if not table_binding.has_current():
            return [], 0

        pool = Pools.get_pool(table_binding.database)
        return await pool.get_records(schema, fields, filters, False, limit, offset, sort)

    @staticmethod
    async def get_history(
        table: str,
        fields: list[str],
        filters: FilterGroups,
        limit: int = 100,
        offset: int = 0,
        sort_by: SortBy = None
    ) -> Tuple[List[dict], int]:
        table_binding: TableBinding = state.TABLES_CONFIG.get(table)
        if table_binding is None:
            raise ValidationError("table not found")

        schema: TableSchema = table_binding.schema

        if not table_binding.has_history():
            return [], 0

        pool = Pools.get_pool(table_binding.history_database)
        return await pool.get_records(schema, fields, filters, True, limit, offset, sort_by)

    @staticmethod
    async def get_grouped(
            table: str,
            is_history: bool,
            fields: list[str],
            filters: FilterGroups,
            group_by: List[str],
            aggs: Aggregates,
            having: FilterGroups,
            limit: int = 100,
            offset: int = 0,
            sort_by: SortBy = None,
    ) -> Tuple[List[dict], int]:
        table_binding: TableBinding = state.TABLES_CONFIG.get(table)
        if table_binding is None:
            raise ValidationError("table not found")

        schema: TableSchema = table_binding.schema

        if (not table_binding.has_current()) and (not is_history):
            raise ValidationError("table doesn't have current")
        if (not table_binding.has_history()) and is_history:
            raise ValidationError("table doesn't have history")

        db_key = table_binding.history_database if is_history else table_binding.database
        pool = Pools.get_pool(db_key)

        return await pool.get_group(schema, is_history, fields, filters, group_by, aggs, having, limit, offset, sort_by)
