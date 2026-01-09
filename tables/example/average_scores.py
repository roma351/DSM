from app.db.field_config import FieldConfig
from app.db.field_types import FieldType
from app.db.table_schema import TableSchema

AverageScoresTable = TableSchema(
        name="average_scores",
        search_fields=[
            FieldConfig("account_id", FieldType.BIGINT, nullable=True),
            FieldConfig("class_id", FieldType.BIGINT, nullable=False),
            FieldConfig("school_id", FieldType.BIGINT, nullable=False),

            FieldConfig("region_id", FieldType.INTEGER, nullable=False),
            FieldConfig("country_id", FieldType.INTEGER, nullable=True),
            FieldConfig("city_id", FieldType.INTEGER, nullable=True),

            FieldConfig("period_id", FieldType.BIGINT, nullable=False),
            FieldConfig("period_year_id", FieldType.INTEGER, nullable=False),
            FieldConfig("period_type", FieldType.INTEGER, nullable=False),
            FieldConfig("period_number", FieldType.INTEGER, nullable=False)
        ],
        data_fields=[
            FieldConfig("score", FieldType.INTEGER, nullable=False),
        ],
        indexes=[
            ("region_id", "school_id", "class_id", "period_year_id", "period_type", "period_number",
             "account_id"),
        ]
    )
