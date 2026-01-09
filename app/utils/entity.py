from typing import Dict, Union

from app.api.errors import ValidationError
from app.db.table_schema import TableSchema
from app.utils.conversion import convert_value

EntityValue = Union[int, str]
EntityDict = Dict[str, EntityValue]


def normalize_entity(schema: TableSchema, entity: EntityDict) -> EntityDict:
    required = [f.name for f in schema.entity_fields]

    for f in schema.entity_fields:
        if f.name not in entity:
            raise ValidationError(f"Missing entity key field '{f.name}' for table '{schema.name}'")

    extras = set(entity.keys()) - set(required)
    if extras:
        raise ValidationError(f"Unknown entity key fields for table '{schema.name}': {sorted(extras)}")

    out: EntityDict = {}
    for f in schema.entity_fields:
        v = convert_value(entity[f.name], f.type)
        if not isinstance(v, (int, str)):
            raise ValidationError(f"Entity key field '{f.name}' must be int|str for table '{schema.name}'")
        out[f.name] = v

    return out
