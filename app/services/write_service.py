from datetime import datetime
from typing import Optional, Dict, Callable

from app.api.errors import ValidationError
from app.db.table_binding import TableBinding
from app.state import state
from app.db.pools import Pools
from app.db.table_schema import TableSchema

from app.utils.conversion import convert_value
from app.utils.entity import EntityDict, normalize_entity


# ============================================================================
# WRITE SERVICE
# ============================================================================


class WriteService:
    @staticmethod
    async def save(
            table: str,
            entity: EntityDict,
            data: dict,
            now: datetime,
    ) -> bool:
        table_binding: TableBinding = state.TABLES_CONFIG.get(table)
        if table_binding is None:
            raise ValidationError("table not found")

        schema: TableSchema = table_binding.schema

        entity_key = normalize_entity(schema, entity)

        pool_current = Pools.get_pool(table_binding.database)
        pool_history = Pools.get_pool(table_binding.history_database)

        save_history_func: Optional[Callable] = None
        if pool_current != pool_history:
            save_history_func = pool_history.save_history

        mode = schema.write_mode

        if mode == "scd":
            if not table_binding.has_current() or not table_binding.has_history():
                raise ValidationError(f"SCD requires current+history for '{schema.name}'")
            return await pool_current.save_scd(
                schema,
                entity_key,
                data,
                now=now,
                save_history_func=save_history_func,
            )

        if mode == "current":
            if not table_binding.has_current():
                raise ValidationError(f"Current mode requires current table for '{schema.name}'")
            return await pool_current.save_current(
                schema,
                entity_key,
                data,
                now=now,
            )

        if mode == "append":
            if not table_binding.has_history():
                raise ValidationError(f"Append mode requires history table for '{schema.name}'")
            return await pool_history.append_history(
                schema,
                entity_key,
                data,
                now=now,
            )

        raise ValidationError(f"Invalid write_mode for '{schema.name}': {mode}")
