import os
from typing import Dict

from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.bootstrap.loader import load_yaml
from app.bootstrap.settings import validate_settings
from app.bootstrap.importer import import_from_path
from app.bootstrap.drivers import get_driver_class

from app.db.table_binding import TableBinding
from app.db.table_schema import TableSchema
from app.db.pools import Pools
from app.db.schema_manager import SchemaManager
from app.middleware.auth_middleware import APIKeyMiddleware
from app.api.endpoints import router as api_router
from app.state import state


def _build_tables_registry(tables_cfg: dict) -> Dict[str, TableBinding]:
    out: Dict[str, TableBinding] = {}

    for table_name, cfg in tables_cfg.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"tables.{table_name} must be a mapping")

        schema_path = cfg.get("schema")
        if not isinstance(schema_path, str) or not schema_path.strip():
            raise ValueError(f"tables.{table_name}.schema is required")

        schema_obj = import_from_path(schema_path)
        if not isinstance(schema_obj, TableSchema):
            raise ValueError(f"tables.{table_name}.schema is not TableSchema: {schema_path}")

        binding = TableBinding(schema=schema_obj)

        if "database" in cfg and cfg["database"] is not None:
            binding.database = cfg["database"]

        if "history_database" in cfg and cfg["history_database"] is not None:
            binding.history_database = cfg["history_database"]

        out[table_name] = binding

    return out


def _build_api_keys_registry(api_keys: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}

    for name, item in api_keys.items():
        if not isinstance(item, dict):
            raise ValueError(f"api_keys.{name} must be a mapping")

        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"api_keys.{name}.key is required")
        if len(key) < 12:
            raise ValueError(f"api_keys.{name}.key length must be more than 12")
        if key[0] in ["$", "{", "}"]:
            raise ValueError(f"api_keys.{name}.key must not have service symbols")

        tables = item.get("tables") or {}
        if not isinstance(tables, dict) or not tables:
            raise ValueError(f"api_keys.{name}.tables must be a non-empty mapping")

        out[key] = tables

    return out


def _build_db_runtime(db_cfg: dict) -> dict[str, dict]:
    # Pools.init ожидает dict pool_name -> config
    # а драйвер-класс можно выбрать внутри Pools.init через get_driver_class
    return db_cfg


def bootstrap_from_env() -> FastAPI:
    db_path = os.getenv("DB_CONFIG_PATH", "./configs/db.yaml")
    api_path = os.getenv("API_KEYS_PATH", "./configs/api_keys.yaml")
    tables_path = os.getenv("TABLES_CONFIG_PATH", "./configs/tables.yaml")

    if not db_path:
        raise ValueError("DB_CONFIG_PATH is required")
    if not api_path:
        raise ValueError("API_KEYS_PATH is required")
    if not tables_path:
        raise ValueError("TABLES_CONFIG_PATH is required")

    raw_db = load_yaml(db_path)
    raw_api = load_yaml(api_path)
    raw_tables = load_yaml(tables_path)

    settings = validate_settings(raw_db, raw_api, raw_tables)

    # store globals
    state.DB_CONFIG = _build_db_runtime(settings.db)
    state.API_KEYS = _build_api_keys_registry(settings.api_keys)
    state.TABLES_CONFIG = _build_tables_registry(settings.tables)
    state.BOOTSTRAPPED = True

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await Pools.init(state.DB_CONFIG, get_driver_class)
        await SchemaManager.init_tables()
        yield
        await Pools.close()

    app = FastAPI(title="Data Snapshots Service", lifespan=lifespan)
    app.include_router(api_router)
    app.add_middleware(APIKeyMiddleware)

    return app
