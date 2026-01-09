from typing import Optional, Tuple, List, Dict

from app.db.table_binding import TableBinding
from app.db.table_schema import TableSchema
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
        table_binding: TableBinding = state.TABLES_CONFIG[table]
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
        sort: str = "desc"
    ) -> Tuple[List[dict], int]:
        table_binding: TableBinding = state.TABLES_CONFIG[table]
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
        sort: str = "desc"
    ) -> Tuple[List[dict], int]:
        table_binding: TableBinding = state.TABLES_CONFIG[table]
        schema: TableSchema = table_binding.schema

        if not table_binding.has_history():
            return [], 0

        pool = Pools.get_pool(table_binding.history_database)
        return await pool.get_records(schema, fields, filters, True, limit, offset, sort)
