from typing import Type

from app.db.database import DatabaseDriver


def get_driver_class(driver: str) -> Type[DatabaseDriver]:
    if driver == "postgres":
        from app.db.drivers.postgres import PostgresDriver
        return PostgresDriver
    if driver == "clickhouse":
        from app.db.drivers.clickhouse import ClickHouseDriver
        return ClickHouseDriver
    raise ValueError(f"Unknown driver '{driver}'")
