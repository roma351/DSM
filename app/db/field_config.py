from typing import Optional
from dataclasses import dataclass, field

from app.db.field_types import FieldType


@dataclass
class FieldConfig:
    name: str
    type: FieldType
    size: Optional[int] = None
    nullable: bool = True

    def __post_init__(self):
        if self.type in (FieldType.VARCHAR, FieldType.CHAR):
            if self.size is None:
                raise ValueError(
                    f"{self.type} requires size parameter for field '{self.name}'"
                )

        if self.type in (FieldType.BIGINT, FieldType.INTEGER, FieldType.DATE,
                         FieldType.TIMESTAMP, FieldType.JSONB, FieldType.TEXT):
            if self.size is not None:
                raise ValueError(
                    f"{self.type} does not accept size parameter for field '{self.name}'"
                )
