# /app/db/postgres.py
import logging
from typing import List, Optional, Tuple, Callable, Any, Literal, Dict
from datetime import datetime
import json

import asyncpg

from app.api.errors import ValidationError
from app.db.table_schema import TableSchema, FieldConfig
from app.db.field_types import FieldType
from app.db.database import DatabaseDriver
from app.models.agg_types import Aggregates, AggOp
from app.models.filter_types import FilterGroups, FilterClause
from app.models.query_types import SortBy, SortItem
from app.utils.conversion import convert_value
from app.utils.hashing import compute_hash
from app.utils.sql_ident import validate_sql_alias, validate_sql_col

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
            sort_by: SortBy = None,
    ) -> Tuple[List[dict], int]:
        table = f"{schema.name}{schema.history_prefix if is_history else schema.current_prefix}"

        if not sort_by:
            raise ValidationError("sort_by is required")

        select_sql = _build_select_fields_pg(schema, is_history=is_history, fields=fields or [])
        where_sql, where_vals, next_idx = _build_where_pg(schema, is_history=is_history, filters=filters, start_idx=1)
        where = f"WHERE {where_sql}" if where_sql else ""

        order_sql = _build_order_by_pg(schema, is_history=is_history, sort_by=sort_by)

        count_sql = f"SELECT COUNT(*) FROM {table} {where}"
        data_sql = f"SELECT {select_sql} FROM {table} {where} {order_sql} LIMIT ${next_idx} OFFSET ${next_idx + 1}"
        vals_q = list(where_vals) + [limit, offset]

        # logging.warning(f"POSTGRES GET_RECORDS: {data_sql}")

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(count_sql, *where_vals)
            rows = await conn.fetch(data_sql, *vals_q)
            return [dict(r) for r in rows], int(total)

    async def get_group(
            self,
            schema: TableSchema,
            is_history: bool,
            filters: FilterGroups,
            group_by: List[str],
            aggs: Aggregates,
            having: FilterGroups,
            limit: int = 100,
            offset: int = 0,
            sort_by: SortBy = None,
    ) -> Tuple[List[dict], int]:
        table = f"{schema.name}{schema.history_prefix if is_history else schema.current_prefix}"

        if not sort_by:
            raise ValidationError("sort_by is required")
        if len(aggs) == 0:
            raise ValidationError("aggs is required")

        allowed_cols = _all_schema_fields(schema, is_history)
        if not group_by:
            raise ValidationError("group_by is required")
        for c in group_by:
            if c not in allowed_cols:
                raise ValidationError(f"Unknown group_by field '{c}' for table '{schema.name}'")

        aggs_sql, agg_aliases, agg_expr_map = _build_aggs_pg(schema, is_history, aggs)

        output_allowed = set(group_by) | set(agg_aliases)

        for s in sort_by:
            if s.field not in output_allowed:
                raise ValidationError(f"Unknown sort_by field '{s.field}' (allowed: group_by + agg aliases)")

        select_list = ", ".join(group_by) + (", " if group_by else "") + aggs_sql

        where_sql, where_vals, idx = _build_where_pg(schema, is_history, filters, start_idx=1)

        having_sql, having_vals, idx = _build_having_pg(
            having,
            output_allowed=output_allowed,
            agg_expr_map=agg_expr_map,
            start_idx=idx,
        )

        where = f"WHERE {where_sql}" if where_sql else ""
        group_sql = "GROUP BY " + ", ".join(group_by) if group_by else ""
        having_clause = f"HAVING {having_sql}" if having_sql else ""

        order_sql = _build_order_by_pg(schema, is_history=is_history, sort_by=sort_by, allowed_extra=output_allowed)

        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM {table}
                {where}
                {group_sql}
                {having_clause}
            ) t
        """

        data_sql = f"""
            SELECT {select_list}
            FROM {table}
            {where}
            {group_sql}
            {having_clause}
            {order_sql}
            LIMIT ${idx} OFFSET ${idx + 1}
        """

        vals_all = list(where_vals) + list(having_vals)
        vals_q = vals_all + [limit, offset]

        # logging.warning(f"POSTGRES GET_GROUP: {data_sql}")
        # logging.warning(f"POSTGRES GET_GROUP2: {count_sql}")

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(count_sql, *vals_all)
            rows = await conn.fetch(data_sql, *vals_q)
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
                        raise ValidationError(f"Index field '{col}' not found in {schema.name} fields")

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
            raise ValidationError("Invalid filter dict, expected {op, value}")
        return op, val
    # pydantic FilterClause-like
    if hasattr(expr, "op") and hasattr(expr, "value"):
        return getattr(expr, "op"), getattr(expr, "value")
    raise ValidationError("Invalid filter expression")


def _all_schema_fields(schema: TableSchema, is_history: bool) -> Dict[str, FieldConfig]:
    return {f.name: f for f in schema.get_all_fields(is_history=is_history)}


def _build_select_fields_pg(schema: TableSchema, is_history: bool, fields: List[str]) -> str:
    if not fields:
        return "*"

    allowed = _all_schema_fields(schema, is_history)
    for c in fields:
        if c not in allowed:
            raise ValidationError(f"Unknown field '{c}' for table '{schema.name}'")
    return ", ".join(fields)


def _build_order_by_pg(schema: TableSchema, is_history: bool, sort_by: SortBy, allowed_extra: Optional[set[str]] = None) -> str:
    allowed = set(_all_schema_fields(schema, is_history).keys())
    if allowed_extra:
        allowed |= set(allowed_extra)

    parts: List[str] = []
    for s in sort_by:
        validate_sql_col(s.field)
        if s.field not in allowed:
            raise ValidationError(f"Unknown sort field '{s.field}' for table '{schema.name}'")
        d = "ASC" if s.dir == "asc" else "DESC"
        parts.append(f"{s.field} {d}")

    return "ORDER BY " + ", ".join(parts) if parts else ""


def _pg_op(op: str) -> str:
    if op in ("=", "!=", "<", "<=", ">", ">="):
        return op
    if op == "in":
        return "IN"
    raise ValidationError(f"Unsupported filter op: {op}")


def _build_where_pg(schema: TableSchema, is_history: bool, filters: FilterGroups, *, start_idx: int = 1,
                    allowed_fields: Optional[Dict[str, FieldConfig]] = None) -> Tuple[str, List[Any], int]:
    """
    groups: OR between groups, AND inside a group
    Returns: (sql_without_where_keyword_or_empty, values, next_idx)
    """
    values: List[Any] = []
    idx = start_idx

    if allowed_fields is None:
        allowed_fields = _all_schema_fields(schema, is_history)

    or_groups: List[str] = []
    for g in filters:
        and_parts: List[str] = []
        for name, clause in g.items():
            if name not in allowed_fields:
                raise ValidationError(f"Unknown filter field '{name}' for table '{schema.name}'")

            if isinstance(clause, FilterClause):
                op = clause.op
                raw_val = clause.value
            else:
                # normalize_filters should have converted everything to FilterClause, but keep safe
                op = "="
                raw_val = clause

            fcfg = allowed_fields[name]
            sql_op = _pg_op(op)

            if op == "in":
                if not isinstance(raw_val, list):
                    raise ValidationError(f"Filter '{name}' with op=in must be a list")
                and_parts.append(f"{name} = ANY(${idx})")
                values.append([convert_value(x, fcfg.type) for x in raw_val])
                idx += 1
            else:
                and_parts.append(f"{name} {sql_op} ${idx}")
                values.append(convert_value(raw_val, fcfg.type))
                idx += 1

        or_groups.append("(" + " AND ".join(and_parts) + ")")

    if not or_groups:
        return "", values, idx

    return " OR ".join(or_groups), values, idx


# postgres.py (top-level)
PG_CUSTOM_AGGS: dict[str, callable] = {
    # пример (если когда-нибудь понадобится):
    # "approx_count_distinct": lambda col: f"approx_count_distinct({col})",
}

def _build_aggs_pg(schema, is_history: bool, aggs: Aggregates) -> Tuple[str, List[str], Dict[str, str]]:
    parts: List[str] = []
    aliases: List[str] = []
    expr_map: Dict[str, str] = {}

    allowed_cols = set(_all_schema_fields(schema, is_history).keys())

    for alias, spec in aggs.items():
        alias = validate_sql_alias(alias)

        if spec.op == AggOp.count:
            expr = "COUNT(*)" if not spec.field else f"COUNT({validate_sql_col(spec.field)})"

        elif spec.op == AggOp.uniq:
            col = validate_sql_col(spec.field or "")
            if col not in allowed_cols:
                raise ValidationError(f"Unknown aggregate field '{col}'")
            expr = f"COUNT(DISTINCT {col})"

        elif spec.op == AggOp.custom:
            name = spec.custom_name or ""
            # pydantic уже regex проверил, но whitelist — здесь
            fn = PG_CUSTOM_AGGS.get(name)
            if not fn:
                raise ValidationError(f"Unsupported custom aggregate '{name}' for postgres")
            # обычно кастомным тоже нужен field (но решай сам):
            if not spec.field:
                raise ValidationError(f"Custom aggregate '{name}' requires 'field'")
            col = validate_sql_col(spec.field)
            if col not in allowed_cols:
                raise ValidationError(f"Unknown aggregate field '{col}'")
            expr = fn(col)

        else:
            col = validate_sql_col(spec.field or "")
            if col not in allowed_cols:
                raise ValidationError(f"Unknown aggregate field '{col}'")
            fn = spec.op.value.upper()
            expr = f"{fn}({col})"

        parts.append(f"{expr} AS {alias}")
        aliases.append(alias)
        expr_map[alias] = expr

    return ", ".join(parts), aliases, expr_map


def _build_having_pg(
    having: FilterGroups,
    *,
    output_allowed: set[str],
    agg_expr_map: Dict[str, str],
    start_idx: int,
) -> Tuple[str, List[Any], int]:
    values: List[Any] = []
    idx = start_idx
    or_groups: List[str] = []

    for g in having:
        and_parts: List[str] = []
        for name, clause in g.items():
            validate_sql_col(name)
            if name not in output_allowed:
                raise ValidationError(f"Unknown having field '{name}' (allowed: group_by + agg aliases)")

            if isinstance(clause, FilterClause):
                op = clause.op
                raw_val = clause.value
            else:
                op = "="
                raw_val = clause

            sql_op = _pg_op(op)

            # expression: either group field name or agg expression
            expr = agg_expr_map.get(name, name)

            if op == "in":
                if not isinstance(raw_val, list):
                    raise ValidationError(f"Having '{name}' with op=in must be a list")
                and_parts.append(f"{expr} = ANY(${idx})")
                values.append(list(raw_val))
                idx += 1
            else:
                and_parts.append(f"{expr} {sql_op} ${idx}")
                values.append(raw_val)
                idx += 1

        or_groups.append("(" + " AND ".join(and_parts) + ")")

    if not or_groups:
        return "", values, idx

    return (" OR ".join(or_groups)), values, idx
