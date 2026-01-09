# /app/db/clickhouse.py
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, cast, Literal

from asynch import Pool
from asynch.cursors import DictCursor

from app.db.database import DatabaseDriver
from app.db.table_schema import TableSchema, FieldConfig
from app.db.field_types import FieldType
from app.models.filter_types import FilterGroups
from app.utils.conversion import convert_value
from app.utils.hashing import compute_hash

InsertMode = Literal["sparse", "schema"]
EntityKey = Dict[str, int | str]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ch_base_type(ft: FieldType) -> str:
    if ft == FieldType.BIGINT:
        return "Int64"
    if ft == FieldType.INTEGER:
        return "Int32"
    if ft == FieldType.NUMERIC:
        return "Float64"
    if ft == FieldType.DATE:
        return "Date"
    if ft == FieldType.TIMESTAMP:
        return "DateTime64(3, 'UTC')"
    if ft in (FieldType.VARCHAR, FieldType.CHAR, FieldType.TEXT, FieldType.JSONB):
        return "String"
    raise ValueError(f"Unsupported FieldType: {ft}")


def _ch_value(v: Any) -> Any:
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, datetime):
        return _ensure_utc(v)
    return v


def _entity_cols(schema: TableSchema) -> List[str]:
    return [f.name for f in schema.entity_fields]


def _build_where_ch(schema: TableSchema, entity: EntityKey) -> Tuple[str, Dict[str, Any]]:
    parts: List[str] = []
    params: Dict[str, Any] = {}
    for f in schema.entity_fields:
        pname = f"e_{f.name}"
        parts.append(f"`{f.name}` = {{{pname}}}")
        params[pname] = entity[f.name]
    return " AND ".join(parts), params


def _prepare_data(schema: TableSchema, data: dict) -> Tuple[dict, dict]:
    search_fields_data = {f.name: convert_value(data[f.name], f.type) for f in schema.search_fields if f.name in data}

    search_data_fields_data: dict = {}
    for f in schema.data_fields:
        if f.name in data:
            val = data[f.name]
            if f.type == FieldType.JSONB:
                val = val if isinstance(val, dict) else json.loads(val)
            else:
                val = convert_value(val, f.type)
            search_data_fields_data[f.name] = val

    return search_fields_data, search_data_fields_data


def _build_row(
    schema: TableSchema,
    *,
    entity: EntityKey,
    fields_data: dict,
    data_fields_data: dict,
    now: datetime,
    is_history: bool,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    archived_at: Optional[datetime] = None,
    data_hash: Optional[str] = None,
    used_count: Optional[int] = None,
    change_count: Optional[int] = None,
) -> Dict[str, Any]:
    cols = [f.name for f in schema.get_all_fields(is_history=is_history)]
    colset = set(cols)

    row: Dict[str, Any] = {}

    for k, v in entity.items():
        row[k] = v

    row.update(fields_data)
    row.update(data_fields_data)

    # hash
    if "data_hash" in colset:
        row["data_hash"] = data_hash if data_hash is not None else compute_hash(data_fields_data)

    # counters
    used_col = schema.field_used_count_name
    change_col = schema.field_change_count_name

    if schema.field_used_count and (used_col in colset) and (used_count is not None):
        row[used_col] = int(used_count)

    if (not is_history) and schema.field_change_count and (change_col in colset) and (change_count is not None):
        row[change_col] = int(change_count)

    # timestamps
    if "created_at" in colset:
        row["created_at"] = _ensure_utc(created_at or now)

    if is_history and ("archived_at" in colset):
        row["archived_at"] = _ensure_utc(archived_at or now)

    if (not is_history) and ("updated_at" in colset):
        row["updated_at"] = _ensure_utc(updated_at or now)

    # schema-mode stability: ensure all columns exist in dict (missing -> None)
    # (in insert_* we do r.get(c) anyway, so this is optional; but helps for clarity)
    return row


class ClickHouseDriver(DatabaseDriver):
    def __init__(self, config: dict):
        super().__init__(config)
        self.config = config
        self.pool: Optional[Pool] = None

        batching = self.config.get("batching") or {}
        self._batch_enabled: bool = bool(batching.get("enabled", False))
        self._flush_interval: float = float(batching.get("flush_interval_sec", 1))
        self._max_batch_size: int = int(batching.get("max_batch_size", 1000))

        self._buffers: dict[str, tuple[list[str], list[tuple]]] = {}
        self._buf_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

    def to_sql(self, field: FieldConfig) -> str:
        base = _ch_base_type(field.type)

        if getattr(field, "lowCardinality", False):
            base = f"LowCardinality({base})"

        if getattr(field, "nullable", True):
            base = f"Nullable({base})"

        return f"`{field.name}` {base}"

    def _partition_by_sql(self, schema: TableSchema) -> str:
        part = getattr(schema, "partition_by", None)
        if isinstance(part, str) and part.strip():
            return part.strip()
        return ""

    def _ttl_sql(self, schema: TableSchema) -> str:
        ttl_expr = getattr(schema, "ttl_expression", None)
        if isinstance(ttl_expr, str) and ttl_expr.strip():
            s = ttl_expr.strip()
            return s[3:].strip() if s.upper().startswith("TTL ") else s

        ttl_days = schema.history_ttl_days
        if ttl_days > 0:
            return f"created_at + INTERVAL {ttl_days} DAY DELETE"

        return ""

    def _db_table(self, schema: TableSchema, *, is_history: bool) -> str:
        suffix = schema.history_prefix if is_history else schema.current_prefix
        return f"{self.config['database']}.{schema.name}{suffix}"

    def _order_by_sql(self, schema: TableSchema, is_history: bool) -> str:
        keys = [f"`{f.name}`" for f in schema.entity_fields]
        if is_history:
            return f"({', '.join(keys + ['created_at'])})"
        return f"({', '.join(keys)})"

    async def get_current(self, schema: TableSchema, entity: EntityKey) -> Optional[dict]:
        table = self._db_table(schema, is_history=False)
        where, params = _build_where_ch(schema, entity)

        order_col = "updated_at" if any(f.name == "updated_at" for f in schema.get_all_fields(is_history=False)) else "created_at"

        sql = f"""
            SELECT *
            FROM {table}
            WHERE {where}
            ORDER BY {order_col} DESC
            LIMIT 1
        """

        async with self.pool.connection() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return dict(row) if row else None

    async def save_scd(
        self,
        schema: TableSchema,
        entity: EntityKey,
        data: dict,
        now: datetime,
        save_history_func: Optional[Callable] = None,
    ) -> bool:
        now = _ensure_utc(now)

        current = await self.get_current(schema=schema, entity=entity)
        fields_data, data_fields_data = _prepare_data(schema, data)
        data_hash = compute_hash(data_fields_data)

        used_col = schema.field_used_count_name
        change_col = schema.field_change_count_name

        # unchanged -> touch updated_at (+ used_count)
        if current and current.get("data_hash") == data_hash:
            if schema.touch_updated_at:
                used_next: Optional[int] = None
                if schema.field_used_count:
                    used_next = int(current.get(used_col, 0)) + 1

                row = _build_row(
                    schema=schema,
                    entity=entity,
                    fields_data=fields_data,
                    data_fields_data=data_fields_data,
                    now=now,
                    is_history=False,
                    created_at=_ensure_utc(cast(datetime, current.get("created_at") or now)),
                    updated_at=now,
                    data_hash=data_hash,
                    used_count=used_next,
                    change_count=None,
                )
                await self.insert_current_batch(schema, [row])
            return False

        # data changed -> archive old version
        if current:
            if save_history_func:
                await save_history_func(None, dict(current), schema, now)
            else:
                await self.save_history(None, dict(current), schema, now)

        if not schema.touch_updated_at:
            return False

        # counters on change:
        # - change_count += 1 (current only)
        # - used_count = 0
        change_next: Optional[int] = None
        used_reset: Optional[int] = None

        if schema.field_change_count:
            change_next = int((current or {}).get(change_col, 0)) + 1
        if schema.field_used_count:
            used_reset = 0

        row = _build_row(
            schema=schema,
            entity=entity,
            fields_data=fields_data,
            data_fields_data=data_fields_data,
            now=now,
            is_history=False,
            created_at=now,
            updated_at=now,
            data_hash=data_hash,
            used_count=used_reset,
            change_count=change_next,
        )
        await self.insert_current_batch(schema, [row])
        return True

    async def save_current(
        self,
        schema: TableSchema,
        entity: EntityKey,
        data: dict,
        now: datetime,
    ) -> bool:
        now = _ensure_utc(now)

        fields_data, data_fields_data = _prepare_data(schema, data)
        data_hash = compute_hash(data_fields_data)

        current = await self.get_current(schema=schema, entity=entity)

        used_col = schema.field_used_count_name
        change_col = schema.field_change_count_name

        # 2.2 insert-only idempotent
        if schema.touch_updated_at is False:
            if current:
                return False

            row = _build_row(
                schema=schema,
                entity=entity,
                fields_data=fields_data,
                data_fields_data=data_fields_data,
                now=now,
                is_history=False,
                created_at=now,
                updated_at=now,
                data_hash=data_hash,
                used_count=0 if schema.field_used_count else None,
                change_count=0 if schema.field_change_count else None,
            )
            await self.insert_current_batch(schema, [row])
            return True

        # unchanged -> touch updated_at (+ used_count)
        if current and current.get("data_hash") == data_hash:
            used_next: Optional[int] = None
            if schema.field_used_count:
                used_next = int(current.get(used_col, 0)) + 1

            row = _build_row(
                schema=schema,
                entity=entity,
                fields_data=fields_data,
                data_fields_data=data_fields_data,
                now=now,
                is_history=False,
                created_at=_ensure_utc(cast(datetime, current.get("created_at") or now)),
                updated_at=now,
                data_hash=data_hash,
                used_count=used_next,
                change_count=None,
            )
            await self.insert_current_batch(schema, [row])
            return False

        # data changed -> update current + counters
        change_next: Optional[int] = None
        used_reset: Optional[int] = None

        if schema.field_change_count:
            change_next = int((current or {}).get(change_col, 0)) + 1
        if schema.field_used_count:
            used_reset = 0

        row = _build_row(
            schema=schema,
            entity=entity,
            fields_data=fields_data,
            data_fields_data=data_fields_data,
            now=now,
            is_history=False,
            created_at=now,
            updated_at=now,
            data_hash=data_hash,
            used_count=used_reset,
            change_count=change_next,
        )
        await self.insert_current_batch(schema, [row])
        return True

    async def append_history(
            self,
            schema: TableSchema,
            entity: EntityKey,
            data: dict,
            now: datetime,
    ) -> bool:
        now = _ensure_utc(now)

        fields_data, data_fields_data = _prepare_data(schema, data)

        cols = [f.name for f in schema.get_all_fields(is_history=True)]
        colset = set(cols)

        # If schema.write_mode == "append", get_all_fields(is_history=True) обычно не содержит:
        # data_hash / archived_at / etc. Поэтому _build_row сам аккуратно заполнит только то, что есть.

        used_col = schema.field_used_count_name
        used_val: Optional[int] = None
        if schema.field_used_count and (used_col in colset):
            used_val = 0

        # data_hash будет вычислен внутри _build_row только если колонка есть в colset
        row = _build_row(
            schema=schema,
            entity=entity,
            fields_data=fields_data,
            data_fields_data=data_fields_data,
            now=now,
            is_history=True,
            created_at=now,
            archived_at=now,
            used_count=used_val,
            change_count=None,
            data_hash=None,
        )

        await self.insert_history_batch(schema, [row])
        return True

    async def save_history(self, conn, current: dict, schema: TableSchema, now: datetime) -> None:
        now = _ensure_utc(now)

        entity: EntityKey = {}
        for f in schema.entity_fields:
            v = current.get(f.name)
            if not isinstance(v, (int, str)):
                raise ValueError(f"Invalid entity key '{f.name}' in current row for '{schema.name}'")
            entity[f.name] = v

        search_fields_data = {f.name: current.get(f.name) for f in schema.search_fields if f.name in current}
        data_fields_data = {f.name: current.get(f.name) for f in schema.data_fields if f.name in current}

        created_at = _ensure_utc(cast(datetime, current.get("created_at") or now))

        data_hash = cast(Optional[str], current.get("data_hash")) or compute_hash(data_fields_data)

        used_col = schema.field_used_count_name
        used_snapshot: Optional[int] = None
        if schema.field_used_count:
            used_snapshot = int(current.get(used_col, 0))

        row = _build_row(
            schema=schema,
            entity=entity,
            fields_data=search_fields_data,
            data_fields_data=data_fields_data,
            now=now,
            is_history=True,
            created_at=created_at,
            archived_at=now,
            data_hash=data_hash,
            used_count=used_snapshot,
            change_count=None,
        )
        await self.insert_history_batch(schema, [row])

    async def insert_current_batch(self, schema: TableSchema, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return

        table = self._db_table(schema, is_history=False)
        cols = [f.name for f in schema.get_all_fields(is_history=False)]

        tuples: List[Tuple[Any, ...]] = []
        for r in rows:
            tuples.append(tuple(_ch_value(r.get(c)) for c in cols))

        sql = f"INSERT INTO {table} ({', '.join(f'`{c}`' for c in cols)}) VALUES"
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuples)

    async def insert_history_batch(self, schema: TableSchema, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return

        table = self._db_table(schema, is_history=True)
        cols = [f.name for f in schema.get_all_fields(is_history=True)]

        tuples: List[Tuple[Any, ...]] = []
        for r in rows:
            tuples.append(tuple(_ch_value(r.get(c)) for c in cols))

        if self._batch_enabled:
            await self._enqueue_insert(table, cols, tuples)
            return

        await self._execute_insert(table, cols, tuples)

    async def get_records(
            self,
            schema: TableSchema,
            fields: list[str],
            filters: FilterGroups,
            is_history: bool,
            limit: int = 100,
            offset: int = 0,
            sort: str = "desc",
    ) -> Tuple[List[dict], int]:
        table = self._db_table(schema, is_history=is_history)

        where_sql, params = _build_filters_where_ch(schema, filters, is_history=is_history)

        all_cols = {f.name for f in schema.get_all_fields(is_history=is_history)}

        if is_history:
            order_col = "created_at" if "created_at" in all_cols else _entity_cols(schema)[0]
        else:
            order_col = "updated_at" if "updated_at" in all_cols else "created_at"

        if not (sort.upper() in ["DESC", "ASC"]):
            raise ValueError("Sort should be 'asc' or 'desc'")

        order_sql = f"ORDER BY {order_col} {sort.upper()}"

        select_sql = "*"
        if fields:
            all_fields = {f.name for f in schema.get_all_fields(is_history=is_history)}
            unknown = [f for f in fields if f not in all_fields]
            if unknown:
                raise ValueError(f"Unknown fields requested: {unknown}")
            select_sql = ", ".join(f"`{f}`" for f in fields)

        count_sql = f"SELECT count() AS cnt FROM {table} {where_sql}"
        data_sql = f"SELECT {select_sql} FROM {table} {where_sql} {order_sql} LIMIT {int(limit)} OFFSET {int(offset)}"

        # logging.warning(f"CLICKHOUSE GET_RECORDS: {data_sql}")

        async with self.pool.connection() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(count_sql, params)
                cnt_row = await cur.fetchone()
                total = int((cnt_row or {}).get("cnt", 0))

                await cur.execute(data_sql, params)
                rows = await cur.fetchall()
                return [dict(r) for r in rows], total

    async def create_table(self, schema: TableSchema, is_history: bool = False) -> None:
        table = self._db_table(schema, is_history=is_history)
        cols = schema.get_all_fields(is_history=is_history)
        cols_sql = ",\n                ".join(self.to_sql(c) for c in cols)

        engine = "MergeTree" if is_history else "ReplacingMergeTree(updated_at)"
        order_by = self._order_by_sql(schema, is_history=is_history)

        partition_by = self._partition_by_sql(schema)
        partition_clause = f"\n            PARTITION BY {partition_by}" if partition_by else ""

        ttl = self._ttl_sql(schema) if is_history else ""
        ttl_clause = f"\n            TTL {ttl}" if ttl else ""

        sql = f"""
            CREATE TABLE IF NOT EXISTS {table}
            (
                {cols_sql}
            )
            ENGINE = {engine}{partition_clause}
            ORDER BY {order_by}{ttl_clause}
        """

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)

    async def verify_indexes(self, schema: TableSchema, is_history: bool = False) -> None:
        return

    async def _enqueue_insert(self, table: str, cols: list[str], tuples: list[tuple]) -> None:
        flush_now: Optional[Tuple[str, list[str], list[tuple]]] = None

        async with self._buf_lock:
            if table in self._buffers:
                old_cols, old_rows = self._buffers[table]
                if old_cols != cols:
                    flush_now = (table, old_cols, old_rows)
                    self._buffers[table] = (cols, list(tuples))
                else:
                    old_rows.extend(tuples)
            else:
                self._buffers[table] = (cols, list(tuples))

            if table in self._buffers and len(self._buffers[table][1]) >= self._max_batch_size:
                c2, r2 = self._buffers.pop(table)
                flush_now = (table, c2, r2)

        if flush_now:
            t, c, r = flush_now
            await self._execute_insert(t, c, r)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush_all()

    async def _flush_all(self) -> None:
        async with self._buf_lock:
            pending = self._buffers
            self._buffers = {}

        for t, (cols, tuples) in pending.items():
            if tuples:
                await self._execute_insert(t, cols, tuples)

    async def _execute_insert(self, table: str, cols: list[str], tuples: list[tuple]) -> None:
        sql = f"INSERT INTO {table} ({', '.join(f'`{c}`' for c in cols)}) VALUES"
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuples)

    async def connect(self) -> None:
        self.pool = Pool(
            host=self.config["host"],
            port=int(self.config.get("port", 9000)),
            user=self.config.get("user"),
            password=self.config.get("password"),
            database=self.config.get("database", "default"),
            minsize=int(self.config.get("min_size", 1)),
            maxsize=int(self.config.get("max_size", 10)),
        )

        if self._batch_enabled and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def disconnect(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None

        try:
            await self._flush_all()
        except Exception:
            pass

        await self.pool.shutdown()


def _allowed_fields(schema: TableSchema, is_history: bool) -> Dict[str, FieldConfig]:
    out: Dict[str, FieldConfig] = {}

    for f in schema.entity_fields:
        out[f.name] = f
    for f in schema.search_fields:
        out[f.name] = f
    for f in schema.data_fields:
        out[f.name] = f

    for f in schema.get_all_fields(is_history=is_history):
        # мета-поля тоже можно фильтровать
        if f.name in (
            "created_at",
            "updated_at",
            "archived_at",
            "data_hash",
            schema.field_used_count_name,
            schema.field_change_count_name,
        ):
            out[f.name] = f

    return out


def _normalize_filter_expr(expr: Any) -> Tuple[str, Any]:
    if isinstance(expr, (int, str)):
        return "=", expr
    if isinstance(expr, list):
        return "in", expr
    if isinstance(expr, dict):
        op = expr.get("op")
        val = expr.get("value")
        if op is None or val is None:
            raise ValueError("Invalid filter dict, expected {op, value}")
        return op, val
    if hasattr(expr, "op") and hasattr(expr, "value"):
        return getattr(expr, "op"), getattr(expr, "value")
    raise ValueError("Invalid filter expression")


def _build_filters_where_ch(
    schema: TableSchema,
    filters: FilterGroups,
    *,
    is_history: bool,
) -> Tuple[str, Dict[str, Any]]:
    """
    FilterGroups:
      [
        {"name": {"op":"=", "value":"123"}, "up": 1},
        {"name": "456"}
      ]
    -> WHERE (`name` = {f0_name} AND `up` = {f0_up}) OR (`name` = {f1_name})
    """
    allowed = _allowed_fields(schema, is_history=is_history)

    or_parts: List[str] = []
    params: Dict[str, Any] = {}

    for gi, group in enumerate(filters):
        and_parts: List[str] = []

        for col, expr in group.items():
            fcfg = allowed.get(col)
            if not fcfg:
                continue

            op, val = _normalize_filter_expr(expr)

            if op == "in":
                if not isinstance(val, list):
                    raise ValueError(f"Filter '{col}' expects list for op 'in'")

                pname = f"f{gi}_{col}"
                and_parts.append(f"`{col}` IN {{{pname}}}")
                params[pname] = tuple(_ch_value(convert_value(v, fcfg.type)) for v in val)
                continue

            if op not in ("=", "!=", "<", "<=", ">", ">="):
                raise ValueError(f"Unsupported filter op: {op}")

            pname = f"f{gi}_{col}"
            and_parts.append(f"`{col}` {op} {{{pname}}}")
            params[pname] = _ch_value(convert_value(val, fcfg.type))

        if and_parts:
            or_parts.append("(" + " AND ".join(and_parts) + ")")

    if not or_parts:
        return "", {}

    return "WHERE " + " OR ".join(or_parts), params