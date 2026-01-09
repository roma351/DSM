from app.state import state
from app.db.pools import Pools


class SchemaManager:
    @staticmethod
    async def init_tables() -> None:
        for table_key, binding in state.TABLES_CONFIG.items():
            schema = binding.schema

            if binding.has_current():
                pool = Pools.get_pool(binding.database)
                await pool.create_table(schema, is_history=False)
                await pool.verify_indexes(schema, is_history=False)

            if binding.has_history():
                pool2 = Pools.get_pool(binding.history_database)
                await pool2.create_table(schema, is_history=True)
                await pool2.verify_indexes(schema, is_history=True)
