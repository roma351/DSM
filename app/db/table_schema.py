from typing import List, Tuple, Literal
from dataclasses import dataclass, field

from app.db.field_config import FieldConfig
from app.db.field_types import FieldType


@dataclass
class TableSchema:
    name: str
    search_fields: List[FieldConfig]
    data_fields: List[FieldConfig]
    indexes: List[Tuple[str, ...]]

    write_mode: Literal["scd", "current", "append"] = "scd"

    entity_fields: List[FieldConfig] = field(
        default_factory=lambda: [
            FieldConfig("entity_id", FieldType.VARCHAR, 200, nullable=False)
        ]
    )

    history_search_fields: List[FieldConfig] = None

    current_prefix: str = "_current"
    history_prefix: str = "_history"

    field_change_count: bool = False
    # change_count:
    # - only in current
    # - increments ONLY when data_hash changes
    # - equals number of snapshots in history

    field_used_count: bool = False
    # used_count:
    # - increments when updated_at is refreshed without data change
    # - snapshot to history on data change

    field_change_count_name: str = "change_count"
    field_used_count_name: str = "used_count"

    history_ttl_days: int = 0  # only clickhouse
    # auto_export_days: str = None

    touch_updated_at: bool = True

    def get_all_fields(self, is_history: bool = False) -> List[FieldConfig]:
        base: List[FieldConfig] = []

        base.extend(self.entity_fields)
        base.extend(self.search_fields)
        base.extend(self.data_fields)

        if self.write_mode == "append":
            base.append(FieldConfig("created_at", FieldType.TIMESTAMP, nullable=False))
            return base

        if not is_history and self.field_change_count:
            base.append(FieldConfig(self.field_change_count_name, FieldType.INTEGER, nullable=False))

        if self.field_used_count:
            base.append(FieldConfig(self.field_used_count_name, FieldType.INTEGER, nullable=False))

        base.append(FieldConfig("data_hash", FieldType.CHAR, 32, nullable=False))
        base.append(FieldConfig("created_at", FieldType.TIMESTAMP, nullable=False))

        if is_history:
            base.append(FieldConfig("archived_at", FieldType.TIMESTAMP, nullable=False))
        else:
            base.append(FieldConfig("updated_at", FieldType.TIMESTAMP, nullable=False))

        return base

    def __post_init__(self):
        if len(self.entity_fields) == 0:
            raise ValueError(
                f"entity_fields is required"
            )
        if len(self.data_fields) == 0:
            raise ValueError(
                f"data_fields is required"
            )

        allowed_types = {FieldType.CHAR, FieldType.VARCHAR, FieldType.INTEGER, FieldType.BIGINT}

        invalid_entity_fields_by_type = [
            f.name for f in self.entity_fields
            if f.type not in allowed_types
        ]

        if invalid_entity_fields_by_type:
            raise ValueError(
                f"entity_fields '{', '.join(invalid_entity_fields_by_type)}' must be CHAR, VARCHAR, INTEGER or BIGINT"
            )

        invalid_entity_fields_by_nullable = [
            f.name for f in self.entity_fields
            if f.nullable
        ]

        if invalid_entity_fields_by_nullable:
            raise ValueError(
                f"entity_fields '{', '.join(invalid_entity_fields_by_nullable)}' must be not null"
            )

        all_names = (
                [f.name for f in self.entity_fields] +
                [f.name for f in self.search_fields] +
                [f.name for f in self.data_fields]
        )

        if self.field_change_count:
            all_names.append(self.field_change_count_name)
        if self.field_used_count:
            all_names.append(self.field_used_count_name)

        if len(all_names) != len(set(all_names)):
            duplicates = [name for name in all_names if all_names.count(name) > 1]
            raise ValueError(f"Duplicate field names found: {set(duplicates)}")
