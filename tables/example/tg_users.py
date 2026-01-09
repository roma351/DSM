from app.db.field_config import FieldConfig
from app.db.field_types import FieldType
from app.db.table_schema import TableSchema

TgUsers = TableSchema(
        name="tg_users",
        entity_fields=[
            FieldConfig("id", FieldType.BIGINT, nullable=False)
        ],
        search_fields=[

        ],
        data_fields=[
            FieldConfig("first_name", FieldType.VARCHAR, 64, nullable=False),
            FieldConfig("last_name", FieldType.VARCHAR, 64, nullable=False),
            FieldConfig("username", FieldType.VARCHAR, 64, nullable=False),
            FieldConfig("image", FieldType.VARCHAR, 64, nullable=False),
            FieldConfig("is_premium", FieldType.INTEGER, nullable=False)
        ],
        indexes=[
            ("username",),
        ]
    )
