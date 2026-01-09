# /app/db/postgres.py
import logging
from typing import List, Optional, Tuple, Callable, Any, Literal, Dict
from datetime import datetime
import json

import asyncpg

from app.db.table_schema import TableSchema, FieldConfig
from app.db.field_types import FieldType
from app.db.database import DatabaseDriver
from app.models.filter_types import FilterGroups
from app.utils.conversion import convert_value
from app.utils.hashing import compute_hash

InsertMode = Literal["sparse", "schema"]
EntityKey = Dict[str, int | str]


def to_sql(fieldConfig: FieldConfig) -> str:
    type_str = fieldConfig.type.value
    if fieldConfig.size:
        type_str = f"{type_str}({fieldConfig.size})"
    null_str = "NOT NULL" if not fieldConfig.nullable else ""
    return f"{fieldConfig.name} {type_str} {null_str}".strip()


class PostgresDriver(DatabaseDriver):
    pool: asyncpg.Pool
    config: dict

    def __init__(self, config: dict):
        super().__init__(config)
        self.config = config

    async def get_current(self, schema: TableSchema, entity: EntityKey) -> Optional[dict]:
        table_current = f"{schema.name}{schema.current_prefix}"
        where_sql, where_vals = _build_where(schema, entity)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {table_current} WHERE {where_sql}",
                *where_vals,
            )
            return dict(row) if row else None

    async def save_scd(
        self,
        schema: TableSchema,
        entity: EntityKey,
        data: dict,
        now: datetime,
        save_history_func: Optional[Callable] = None,
    ) -> bool:
        table_current = f"{schema.name}{schema.current_prefix}"
        where_sql, where_vals = _build_where(schema, entity)

        fields_data, data_fields_data = _prepare_data(schema, data)
        data_hash = _compute_data_hash(data_fields_data)

        used_col = schema.field_used_count_name
        change_col = schema.field_change_count_name

        has_used = schema.field_used_count
        has_change = schema.field_change_count

        async with self.pool.acquire() as conn:
            current = await conn.fetchrow(
                f"SELECT * FROM {table_current} WHERE {where_sql}",
                *where_vals,
            )

            if current:
                if current.get("data_hash") == data_hash:
                    if has_used:
                        used_next = int(current.get(used_col, 0)) + 1
                        update_data: Dict[str, Any] = {"updated_at": now, used_col: used_next}
                        set_sql, set_vals = _build_update_set(update_data, start_idx=len(where_vals) + 1)
                        await conn.execute(
                            f"UPDATE {table_current} SET {set_sql} WHERE {where_sql}",
                            *where_vals,
                            *set_vals,
                        )
                    else:
                        await conn.execute(
                            f"UPDATE {table_current} SET updated_at = ${len(where_vals) + 1} WHERE {where_sql}",
                            *where_vals,
                            now,
                        )
                    return False

                if save_history_func:
                    await save_history_func(None, dict(current), schema, now)
                else:
                    await self.save_history(conn, dict(current), schema, now)

                if not schema.touch_updated_at:
                    return False

                update_data: Dict[str, Any] = {**fields_data, **data_fields_data}
                if _has_column(schema, is_history=False, col="data_hash"):
                    update_data["data_hash"] = data_hash

                if has_change:
                    update_data[change_col] = int(current.get(change_col, 0)) + 1
                if has_used:
                    update_data[used_col] = 0

                update_data["created_at"] = now
                update_data["updated_at"] = now

                set_sql, set_vals = _build_update_set(update_data, start_idx=len(where_vals) + 1)
                await conn.execute(
                    f"UPDATE {table_current} SET {set_sql} WHERE {where_sql}",
                    *where_vals,
                    *set_vals,
                )
                return True

            cols, vals, placeholders = _build_insert(
                schema=schema,
                entity=entity,
                fields_data=fields_data,
                data_fields_data=data_fields_data,
                now=now,
                is_history=False,
                mode="schema",
                data_hash=data_hash,
            )
            await conn.execute(
                f"INSERT INTO {table_current} ({', '.join(cols)}) VALUES ({placeholders})",
                *vals,
            )
            return True

    async def save_history(
        self,
        conn,
        current: dict,
        schema: TableSchema,
        now: datetime,
    ) -> None:
        table_history = f"{schema.name}{schema.history_prefix}"

        cols = [f.name for f in schema.get_all_fields(is_history=True)]
        vals: List[Any] = []
        for c in cols:
            if c == "archived_at":
                vals.append(now)
            else:
                vals.append(_jsonify(current.get(c)))

        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))

        if conn:
            await conn.execute(
                f"INSERT INTO {table_history} ({', '.join(cols)}) VALUES ({placeholders})",
                *vals,
            )
        else:
            async with self.pool.acquire() as conn2:
                await conn2.execute(
                    f"INSERT INTO {table_history} ({', '.join(cols)}) VALUES ({placeholders})",
                    *vals,
                )

    async def save_current(
            self,
            schema: TableSchema,
            entity: EntityKey,
            data: dict,
            now: datetime,
    ) -> bool:
        table_current = f"{schema.name}{schema.current_prefix}"
        where_sql, where_vals = _build_where(schema, entity)

        fields_data, data_fields_data = _prepare_data(schema, data)
        data_hash = _compute_data_hash(data_fields_data)

        used_col = schema.field_used_count_name
        change_col = schema.field_change_count_name

        has_used = schema.field_used_count
        has_change = schema.field_change_count

        async with self.pool.acquire() as conn:
            # 2.2 insert-only idempotent
            if schema.touch_updated_at is False:
                cols, vals, placeholders = _build_insert(
                    schema=schema,
                    entity=entity,
                    fields_data=fields_data,
                    data_fields_data=data_fields_data,
                    now=now,
                    is_history=False,
                    mode="sparse",
                    data_hash=data_hash,
                )

                conflict_target = f"({', '.join([f.name for f in schema.entity_fields])})"
                sql = f"""
                    INSERT INTO {table_current} ({', '.join(cols)})
                    VALUES ({placeholders})
                    ON CONFLICT {conflict_target} DO NOTHING
                    RETURNING 1
                """
                row = await conn.fetchrow(sql, *vals)
                return bool(row)

            current = await conn.fetchrow(
                f"SELECT * FROM {table_current} WHERE {where_sql}",
                *where_vals,
            )

            if current:
                if current.get("data_hash") == data_hash:
                    if has_used:
                        used_next = int(current.get(used_col, 0)) + 1
                        update_data: Dict[str, Any] = {"updated_at": now, used_col: used_next}
                        set_sql, set_vals = _build_update_set(update_data, start_idx=len(where_vals) + 1)
                        await conn.execute(
                            f"UPDATE {table_current} SET {set_sql} WHERE {where_sql}",
                            *where_vals,
                            *set_vals,
                        )
                    else:
                        await conn.execute(
                            f"UPDATE {table_current} SET updated_at = ${len(where_vals) + 1} WHERE {where_sql}",
                            *where_vals,
                            now,
                        )
                    return False

                update_data: Dict[str, Any] = {**fields_data, **data_fields_data}
                if _has_column(schema, is_history=False, col="data_hash"):
                    update_data["data_hash"] = data_hash

                if has_change:
                    update_data[change_col] = int(current.get(change_col, 0)) + 1
                if has_used:
                    update_data[used_col] = 0

                update_data["updated_at"] = now

                set_sql, set_vals = _build_update_set(update_data, start_idx=len(where_vals) + 1)
                await conn.execute(
                    f"UPDATE {table_current} SET {set_sql} WHERE {where_sql}",
                    *where_vals,
                    *set_vals,
                )
                return True

            cols, vals, placeholders = _build_insert(
                schema=schema,
                entity=entity,
                fields_data=fields_data,
                data_fields_data=data_fields_data,
                now=now,
                is_history=False,
                mode="schema",
                data_hash=data_hash,
            )
            await conn.execute(
                f"INSERT INTO {table_current} ({', '.join(cols)}) VALUES ({placeholders})",
                *vals,
            )
            return True

    async def append_history(
        self,
        schema: TableSchema,
        entity: EntityKey,
        data: dict,
        now: datetime,
    ) -> bool:
        table_history = f"{schema.name}{schema.history_prefix}"

        fields_data, data_fields_data = _prepare_data(schema, data)
        data_hash = _compute_data_hash(data_fields_data)

        cols, vals, placeholders = _build_insert(
            schema=schema,
            entity=entity,
            fields_data=fields_data,
            data_fields_data=data_fields_data,
            now=now,
            is_history=True,
            mode="schema",
            data_hash=data_hash,
        )

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO {table_history} ({', '.join(cols)}) VALUES ({placeholders})",
                *vals,
            )
        return True

    # --- REPLACE get_query + get_history WITH ONE METHOD ---
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
        table = f"{schema.name}{schema.history_prefix if is_history else schema.current_prefix}"

        where_sql, where_vals, idx = _build_filters_where(
            schema,
            filters,
            is_history=is_history,
            start_idx=1,
        )

        if is_history:
            order_col = "created_at" if _has_column(schema, is_history=True, col="created_at") else \
                schema.entity_fields[0].name
        else:
            if _has_column(schema, is_history=False, col="updated_at"):
                order_col = "updated_at"
            else:
                order_col = "created_at"

        if not (sort.upper() in ["DESC", "ASC"]):
            raise ValueError("Sort should be 'asc' or 'desc'")

        order_sql = f"ORDER BY {order_col} {sort.upper()}"

        select_sql = "*"
        if fields:
            all_fields = {f.name for f in schema.get_all_fields(is_history=is_history)}
            unknown = [f for f in fields if f not in all_fields]
            if unknown:
                raise ValueError(f"Unknown fields requested: {unknown}")
            select_sql = ", ".join(fields)

        count_sql = f"SELECT COUNT(*) FROM {table} {where_sql}"
        data_sql = f"SELECT {select_sql} FROM {table} {where_sql} {order_sql} LIMIT ${idx} OFFSET ${idx + 1}"
        data_vals = where_vals + [limit, offset]

        # logging.warning(f"POSTGRES GET_RECORDS: {data_sql}")

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(count_sql, *where_vals)
            rows = await conn.fetch(data_sql, *data_vals)
            return [dict(r) for r in rows], int(total)

    async def create_table(self, schema: TableSchema, is_history: bool = False) -> None:
        table = f"{schema.name}{schema.history_prefix if is_history else schema.current_prefix}"
        fields = schema.get_all_fields(is_history=is_history)
        fields_sql = ",\n    ".join(to_sql(f) for f in fields)

        entity_cols = [f.name for f in schema.entity_fields]
        pk_cols = entity_cols + (["archived_at"] if is_history and _has_column(schema, is_history=True, col="archived_at") else [])
        pk_sql = f"PRIMARY KEY ({', '.join(pk_cols)})"

        create_sql = f"""
            CREATE TABLE IF NOT EXISTS {table} (
                {fields_sql},
                {pk_sql}
            )
        """

        async with self.pool.acquire() as conn:
            await conn.execute(create_sql)

    async def verify_indexes(self, schema: TableSchema, is_history: bool = False) -> None:
        table = f"{schema.name}{schema.history_prefix}" if is_history else f"{schema.name}{schema.current_prefix}"
        all_fields = list(schema.entity_fields) + list(schema.search_fields) + list(schema.data_fields)

        async with self.pool.acquire() as conn:
            for cols in schema.indexes:
                for col in cols:
                    if not any(f.name == col for f in all_fields):
                        raise ValueError(f"Index field '{col}' not found in {schema.name} fields")

                cols_str = ", ".join(cols)

                where_clauses: List[str] = []
                for col in cols:
                    field = next((f for f in all_fields if f.name == col), None)
                    if field and field.nullable:
                        where_clauses.append(f"{col} IS NOT NULL")

                where = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

                idx_name = (
                    f"idx_{schema.name}_history_{'_'.join(cols)}"
                    if is_history
                    else f"idx_{schema.name}_{'_'.join(cols)}"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols_str}){where}"
                )

            if is_history and _has_column(schema, is_history=True, col="created_at"):
                idx_created = f"idx_{schema.name}_history_created"
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_created} ON {table} (created_at DESC)"
                )

    async def connect(self) -> None:
        dsn = (
            f"postgresql://{self.config['user']}:{self.config['password']}"
            f"@{self.config['host']}:{self.config['port']}/{self.config['database']}"
        )
        self.pool = await asyncpg.create_pool(
            dsn,
            min_size=int(self.config.get("min_size", 2)),
            max_size=int(self.config.get("max_size", 10)),
            ssl=self.config.get("ssl"),
        )

    async def disconnect(self) -> None:
        await self.pool.close()


def _prepare_data(schema: TableSchema, data: dict) -> Tuple[dict, dict]:
    search_fields_data = {f.name: convert_value(data[f.name], f.type) for f in schema.search_fields if f.name in data}

    search_data_fields_data: Dict[str, Any] = {}
    for f in schema.data_fields:
        if f.name in data:
            val = data[f.name]
            if f.type == FieldType.JSONB:
                val = val if isinstance(val, dict) else json.loads(val)
            else:
                val = convert_value(val, f.type)
            search_data_fields_data[f.name] = val

    return search_fields_data, search_data_fields_data


def _compute_data_hash(data_fields_data: dict) -> str:
    return compute_hash(data_fields_data)


def _build_where(schema: TableSchema, entity: EntityKey) -> Tuple[str, List[Any]]:
    parts: List[str] = []
    vals: List[Any] = []
    idx = 1
    for f in schema.entity_fields:
        parts.append(f"{f.name} = ${idx}")
        vals.append(entity[f.name])
        idx += 1
    return " AND ".join(parts), vals


def _build_update_set(update_data: Dict[str, Any], start_idx: int) -> Tuple[str, List[Any]]:
    parts: List[str] = []
    vals: List[Any] = []
    idx = start_idx
    for col, val in update_data.items():
        parts.append(f"{col} = ${idx}")
        vals.append(_jsonify(val))
        idx += 1
    return ", ".join(parts), vals


def _build_insert(
    *,
    schema: TableSchema,
    entity: EntityKey,
    fields_data: dict,
    data_fields_data: dict,
    now: datetime,
    is_history: bool,
    mode: InsertMode,
    data_hash: str,
) -> Tuple[List[str], List[Any], str]:
    row: Dict[str, Any] = {}
    row.update(entity)
    row.update(fields_data)
    row.update(data_fields_data)

    cols = [f.name for f in schema.get_all_fields(is_history=is_history)]

    # meta
    if "created_at" in cols:
        row["created_at"] = now
    if "updated_at" in cols and not is_history:
        row["updated_at"] = now
    if "archived_at" in cols and is_history:
        row["archived_at"] = now
    if "data_hash" in cols:
        row["data_hash"] = data_hash

    # counters init
    used_col = schema.field_used_count_name
    change_col = schema.field_change_count_name

    if schema.field_used_count and used_col in cols and used_col not in row:
        row[used_col] = 0

    # change_count exists only in current by your design (not in history)
    if (not is_history) and schema.field_change_count and change_col in cols and change_col not in row:
        row[change_col] = 0

    if mode == "schema":
        vals = [_jsonify(row.get(c)) for c in cols]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
        return cols, vals, placeholders

    sparse_cols: List[str] = []
    sparse_vals: List[Any] = []

    for f in schema.entity_fields:
        sparse_cols.append(f.name)
        sparse_vals.append(row.get(f.name))

    for f in schema.search_fields:
        if f.name in fields_data:
            sparse_cols.append(f.name)
            sparse_vals.append(_jsonify(fields_data[f.name]))

    for f in schema.data_fields:
        if f.name in data_fields_data:
            sparse_cols.append(f.name)
            sparse_vals.append(_jsonify(data_fields_data[f.name]))

    if used_col in cols and used_col in row:
        sparse_cols.append(used_col)
        sparse_vals.append(row[used_col])

    if (not is_history) and change_col in cols and change_col in row:
        sparse_cols.append(change_col)
        sparse_vals.append(row[change_col])

    for meta in ("data_hash", "created_at", "updated_at", "archived_at"):
        if meta in cols and meta in row:
            sparse_cols.append(meta)
            sparse_vals.append(_jsonify(row[meta]))

    placeholders = ", ".join(f"${i + 1}" for i in range(len(sparse_vals)))
    return sparse_cols, sparse_vals, placeholders


def _has_column(schema: TableSchema, is_history: bool, col: str) -> bool:
    return any(f.name == col for f in schema.get_all_fields(is_history=is_history))


def _jsonify(val: Any) -> Any:
    return json.dumps(val) if isinstance(val, dict) else val


# --- ADD ---
def _allowed_fields(schema: TableSchema, is_history: bool) -> Dict[str, FieldConfig]:
    fields = list(schema.entity_fields) + list(schema.search_fields) + list(schema.data_fields)
    # meta fields can be filtered too (history/query windows rely on them)
    for f in schema.get_all_fields(is_history=is_history):
        if f.name in ("created_at", "updated_at", "archived_at", "data_hash",
                      schema.field_used_count_name, schema.field_change_count_name):
            fields.append(f)

    out: Dict[str, FieldConfig] = {}
    for f in fields:
        out[f.name] = f
    return out


# --- ADD ---
def _normalize_filter_expr(expr: Any) -> Tuple[str, Any]:
    # expr already normalized by pydantic usually, but keep driver robust
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
    # pydantic FilterClause-like
    if hasattr(expr, "op") and hasattr(expr, "value"):
        return getattr(expr, "op"), getattr(expr, "value")
    raise ValueError("Invalid filter expression")


# --- ADD ---
def _build_filters_where(
    schema: TableSchema,
    filters: FilterGroups,
    *,
    is_history: bool,
    start_idx: int = 1,
) -> Tuple[str, List[Any], int]:
    """
    FilterGroups:
      [
        {"name": {"op": "=", "value": "123"}, "up": 1},
        {"name": "456"}
      ]
    => (name='123' AND up=1) OR (name='456')
    """
    allowed = _allowed_fields(schema, is_history=is_history)

    or_parts: List[str] = []
    values: List[Any] = []
    idx = start_idx

    for group in filters:
        and_parts: List[str] = []

        for col, expr in group.items():
            fcfg = allowed.get(col)
            if not fcfg:
                continue  # unknown fields are ignored (like your old logic)

            op, val = _normalize_filter_expr(expr)

            if op == "in":
                if not isinstance(val, list):
                    raise ValueError(f"Filter '{col}' expects list for op 'in'")
                and_parts.append(f"{col} = ANY(${idx})")
                values.append([convert_value(v, fcfg.type) for v in val])
                idx += 1
                continue

            if op not in ("=", "!=", "<", "<=", ">", ">="):
                raise ValueError(f"Unsupported filter op: {op}")

            and_parts.append(f"{col} {op} ${idx}")
            values.append(convert_value(val, fcfg.type))
            idx += 1

        if and_parts:
            or_parts.append("(" + " AND ".join(and_parts) + ")")

    if not or_parts:
        return "", [], idx

    return "WHERE " + " OR ".join(or_parts), values, idx
