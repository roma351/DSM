from typing import Callable, Dict, Type

from app.db.database import DatabaseDriver


class Pools:
    pools: Dict[str, DatabaseDriver] = {}

    @classmethod
    async def init(
        cls,
        db_config: Dict[str, dict],
        get_driver_class: Callable[[str], Type[DatabaseDriver]],
    ) -> None:
        for db_key, config in db_config.items():
            driver_name = config.get("driver")
            if not driver_name:
                raise ValueError(f"db.{db_key}.driver is required")

            driver_cls = get_driver_class(driver_name)

            cfg = dict(config)
            cfg["key"] = db_key

            pool = driver_cls(cfg)
            await pool.connect()
            cls.pools[db_key] = pool

    @classmethod
    async def close(cls) -> None:
        for pool in cls.pools.values():
            await pool.disconnect()
        cls.pools = {}

    @classmethod
    def get_pool(cls, db_name: str = "default") -> DatabaseDriver:
        if db_name not in cls.pools:
            raise KeyError(f"DB pool '{db_name}' is not initialized")
        return cls.pools[db_name]
