from typing import Dict, Optional


class Settings:
    def __init__(self, *, db: Dict[str, dict], api_keys: Dict[str, dict], tables: Dict[str, dict]):
        self.db = db
        self.api_keys = api_keys
        self.tables = tables


def _require(d: dict, key: str) -> str:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError(f"Missing required config key '{key}'")
    return v


def validate_settings(raw_db: dict, raw_api: dict, raw_tables: dict) -> Settings:
    db = raw_db.get("db") or {}
    if not isinstance(db, dict) or not db:
        raise ValueError("db.yaml must contain root key 'db' with mapping")

    api_keys = raw_api.get("api_keys") or {}
    if not isinstance(api_keys, dict) or not api_keys:
        raise ValueError("api_keys.yaml must contain root key 'api_keys' with mapping")

    tables = raw_tables.get("tables") or {}
    if not isinstance(tables, dict) or not tables:
        raise ValueError("tables.yaml must contain root key 'tables' with mapping")

    # validate db pools minimally
    for pool_name, cfg in db.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"db.{pool_name} must be a mapping")
        drv = _require(cfg, "driver")
        if drv not in ("postgres", "clickhouse"):
            raise ValueError(f"db.{pool_name}.driver must be postgres|clickhouse")

        _require(cfg, "host")
        if cfg.get("port") is None:
            raise ValueError(f"db.{pool_name}.port required")
        if cfg.get("database") is None:
            raise ValueError(f"db.{pool_name}.database required")

    # validate tables minimally
    for tname, tcfg in tables.items():
        if not isinstance(tcfg, dict):
            raise ValueError(f"tables.{tname} must be a mapping")
        _require(tcfg, "schema")

    return Settings(db=db, api_keys=api_keys, tables=tables)
