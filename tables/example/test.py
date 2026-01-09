from app.db.field_config import FieldConfig
from app.db.field_types import FieldType
from app.db.table_schema import TableSchema

TestTable = TableSchema(
        name="test",
        entity_fields=[
            FieldConfig("entity_field_str", FieldType.VARCHAR, 200, nullable=False),
            FieldConfig("entity_field_int", FieldType.INTEGER, nullable=False)
        ],
        search_fields=[
            FieldConfig("search_field_str", FieldType.VARCHAR, 50, nullable=False),
            FieldConfig("search_field_int", FieldType.INTEGER, nullable=False),
            FieldConfig("search_field_date", FieldType.DATE, nullable=True),
            FieldConfig("search_field_timestamp", FieldType.TIMESTAMP, nullable=True),
            FieldConfig("search_field_jsonb", FieldType.JSONB, nullable=True)
        ],
        data_fields=[
            FieldConfig("data_field_str", FieldType.VARCHAR, 50, nullable=False),
            FieldConfig("data_field_int", FieldType.INTEGER, nullable=False),
        ],
        indexes=[
            ("search_field_str", "search_field_int"),
        ],
        write_mode="scd",

        field_used_count=True,
        field_change_count=True
)
