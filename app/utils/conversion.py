from typing import Any
from datetime import datetime
import json

from app.db.field_types import FieldType


def convert_value(value: Any, field_type: FieldType) -> Any:
    """Конвертация значения в нужный тип"""
    if value is None:
        return None

    if field_type == FieldType.DATE:
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d").date()
        return value

    elif field_type == FieldType.TIMESTAMP:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    elif field_type in (FieldType.INTEGER, FieldType.BIGINT):
        return int(value)

    elif field_type == FieldType.NUMERIC:
        from decimal import Decimal
        return Decimal(str(value))

    elif field_type == FieldType.JSONB:
        if isinstance(value, str):
            return json.loads(value)
        return value

    return value
