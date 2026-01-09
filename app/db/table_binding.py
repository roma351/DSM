from dataclasses import dataclass
from typing import Optional

from app.db.table_schema import TableSchema


@dataclass
class TableBinding:
    schema: TableSchema

    database: str = "default"
    history_database: str = "default"

    use_current: Optional[bool] = None
    use_history: Optional[bool] = None

    def has_current(self) -> bool:
        # defaults from write_mode
        if self.use_current is not None:
            return self.use_current
        return self.schema.write_mode in ("scd", "current")

    def has_history(self) -> bool:
        if self.use_history is not None:
            return self.use_history
        return self.schema.write_mode in ("scd", "append")
