from enum import Enum


class FieldType(str, Enum):
    BIGINT = "BIGINT"
    INTEGER = "INTEGER"
    VARCHAR = "VARCHAR"
    CHAR = "CHAR"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    NUMERIC = "NUMERIC"
    JSONB = "JSONB"
    TEXT = "TEXT"
    # ARRAY[STRING]
