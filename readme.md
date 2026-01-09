# DSM — Data Snapshots Microservice

**DSM (Data Snapshots Microservice)** is an asynchronous service for storing and reading
versioned data (states, snapshots, events) with strict **write / read separation**.

---

## Table of Contents

- [Configuration & Bootstrap](#configuration--bootstrap)
  - Environment variables
  - Bootstrap process
- [Tables configuration](#tables-configuration)
- [Database configuration](#database-configuration)
- [API keys configuration](#api-keys-configuration)
- [Table schema](#table-schema)
  - Fields model
  - Counters
  - Storage modes
- [API overview](#api-overview)
  - Write API
  - Read API
- [Filters model](#filters-model)
- [Web Admin](#web-admin)
- [TODO](#todo)

---

## Configuration & Bootstrap

DSM is bootstrapped at runtime using configuration files referenced from environment variables.

> No drivers, schemas, or tables are imported implicitly.
> Everything is resolved during bootstrap.

### Required Environment Variables

- `DB_CONFIG_PATH`  
  Path to database configuration YAML

- `TABLES_CONFIG_PATH`  
  Path to tables registry YAML

- `API_KEYS_PATH`  
  Path to API keys YAML

Example:

~~~
export DB_CONFIG_PATH=/etc/dsm/db.yaml
export TABLES_CONFIG_PATH=/etc/dsm/tables.yaml
export API_KEYS_PATH=/etc/dsm/api_keys.yaml
~~~

---

## Bootstrap Process

At application startup:

1. YAML files are loaded from paths in env vars
2. Configs are validated and normalized
3. Runtime registries are built:
   - DB_CONFIG
   - TABLES_CONFIG
   - API_KEYS
4. Database pools are initialized
5. Tables are created / verified
6. FastAPI app is started

All runtime configuration is stored in a global application state object.

---

## Tables Configuration

`tables.yaml` defines **table bindings**, not schemas themselves.

Schemas remain pure Python objects.
Bindings describe how schemas are connected to databases.

Example:

~~~
tables:
  average_scores:
    schema: tables.average_scores:AverageScoresTable
    database: default
    history_database: analytics

  tg_users:
    schema: tables.tg_users:TgUsers
~~~

### Fields

- `schema`  
  Python import path in form:
  `module.path:SchemaVariable`

  Example resolution:

  > tables.average_scores:AverageScoresTable  
  → import tables.average_scores  
  → get attribute AverageScoresTable

- `database` (optional)  
  Database key for current table

- `history_database` (optional)  
  Database key for history table

If `database` or `history_database` is omitted, schema defaults are used.

---

## Database Configuration

Defines database connections.
Drivers are specified as strings and resolved only during bootstrap.

Example:

~~~
databases:
  default:
    driver: postgres
    host: localhost
    port: 5432
    database: dsm
    user: dsm
    password: secret

  analytics:
    driver: clickhouse
    host: localhost
    port: 9000
    database: analytics
    user: default
    password: ""
    batching:
      enabled: true
      flush_interval_sec: 1
      max_batch_size: 1000
~~~

### Notes

- No driver classes are imported in config
- Only drivers referenced in YAML are loaded
- Unused drivers are never imported

---

## API Keys Configuration

Defines API keys and their access rules.

Example:

~~~
api_keys:
  main_key:
    key: ${API_KEY}
    tables:
      _:
        - "*"

  read_key:
    key: ${API_KEY_READ}
    tables:
      _:
        - /read/get
        - /read/query
        - /read/history
        - /config/tables
        - /health
~~~

### Semantics

- `key`  
  API key value (environment variable expansion supported)

- `tables`  
  Mapping:
  - table name or `_` (all tables)
  - list of allowed HTTP paths

This structure is normalized into a runtime registry during bootstrap.

---

## Table Schema

Table schemas are pure Python and define **data structure and behavior**.

They are not responsible for database selection or routing.

Example:

~~~
from app.db.field_types import FieldType
from app.db.table_schema import FieldConfig, TableSchema

AverageScoresTable = TableSchema(
    name="average_scores",

    entity_fields=[
        FieldConfig("entity_id", FieldType.BIGINT, nullable=False),
    ],

    search_fields=[
        FieldConfig("period_id", FieldType.BIGINT, nullable=False),
        FieldConfig("school_id", FieldType.BIGINT, nullable=False),
    ],

    data_fields=[
        FieldConfig("score", FieldType.INTEGER, nullable=False),
    ],

    indexes=[
        ("period_id", "school_id"),
    ],

    write_mode="scd",

    field_change_count=True,
    field_used_count=True,
)
~~~

---

## Fields Model

### entity_fields

Primary key of the entity.
One or multiple fields.

- Always part of current and history
- Used for routing and grouping

---

### search_fields

Filterable fields.

- Used in `/read/query` and `/read/history`
- Indexed logically (not necessarily DB index)
- Do not affect versioning

---

### data_fields

Versioned fields.

- Changes are tracked via `data_hash`
- Used to detect updates vs no-op writes

---

## Counters

### field_change_count

- Column exists **only in current**
- Increments when `data_hash` changes
- Represents number of historical snapshots

### field_used_count

- Column exists in **current and history**
- Increments when:
  - `updated_at` is touched
  - data did NOT change
- Resets to `0` when data changes
- Snapshotted into history on change

---

## Storage Modes

Behavior is defined by `write_mode`.

### scd — Current + History (Slowly Changing Dimensions)

- `_current` holds latest version
- `_history` stores previous versions

Rules:
- No data change:
  - update `updated_at`
  - increment `used_count`
- Data change:
  - snapshot current into history
  - update current
  - increment `change_count`
  - reset `used_count`

---

### current — Current Only

- No history table
- Two behaviors:
  - `touch_updated_at=True`
  - `touch_updated_at=False` (idempotent insert)

---

### append — Append Only

- History table only
- No updates
- Every write is an insert
- Suitable for events, logs, analytics

---

## API Overview

All requests require:

~~~
X-API-KEY: <key>
~~~

All timestamps are UTC.

---

## Write API

### POST /write/save

Unified write endpoint.

Request:

~~~
{
  "table": "average_scores",
  "entity": {"entity_id": 123},
  "data": {
    "score": 42
  },
  "now": "2024-01-01 12:00:00"
}
~~~

Response:

~~~
{
  "ok": true,
  "changed": true
}
~~~

- `changed=false` means idempotent write

---

## Read API

### POST /read/get

Get current entity state.

~~~
{
  "table": "average_scores",
  "entity": {"entity_id": 123}
}
~~~

---

### POST /read/query

Query current table with filters.

~~~
{
  "table": "average_scores",
  "filters": {
    "period_id": 2024
  },
  "limit": 100,
  "offset": 0,
  "sort": "desc"
}
~~~

---

### POST /read/history

Query historical versions.

~~~
{
  "table": "average_scores",
  "filters": {
    "entity_id": 123
  },
  "from_date": "2023-12-01 00:00:00",
  "to_date": "2024-01-01 00:00:00"
}
~~~

History window semantics:

> A version is valid in interval  
> `[created_at, archived_at]`

Applied filters:

- `created_at <= to_date`
- `archived_at >= from_date`

---

## Filters Model

DSM uses **OR-of-AND** filter groups.

Structure:

~~~
FilterGroups = List[FilterGroup]
FilterGroup  = Dict[str, FilterClause]
~~~

Example semantic:

~~~
[
  {"name": "123", "up": 1},
  {"name": "456"}
]
~~~

Means:

> (name = 123 AND up = 1)  
> OR  
> (name = 456)

---

### FilterClause

~~~
{
  "op": "=" | "!=" | "<" | "<=" | ">" | ">=" | "in",
  "value": scalar | list
}
~~~

Shorthand forms are normalized automatically:

- `"field": 10` → `{op: "=", value: 10}`
- `"field": [1,2]` → `{op: "in", value: [1,2]}`

---

### Web Admin
TODO `/admin?set_admin=1`

---

## TODO

```text
————————————————————————————————————————————————————
-. в Query и History добавить group = ["client_id", "command_type"] (GROUP BY) и sort_by = "created_at/updated_at" + визуализация и графики
1. только по Query 2. только по History
-. генератор файлов (python, golang) (tg_users_write()) (tg_users_query())...
- проверку ключа переделать и проверку для него прав и таблиц
————————————————————————————————————————————————————
————————————————————————————————————————————————————

написать тесты, при подключении к бд и работы с таблицами
проверить и проработать индексы

-----
актив промокоды (append)
актив рефки (append)
актив подписки (scd)
статусы платеж счетов (current)
логи (append)
статистика (scd, append)
история адверт (-)

classmates_names (current)
entity_id
-
last_name first_name

——
refs_users (current)
entity_id
-
ref

——
users_used_commands (не для всех команд) (scd)
entity_id source command
как в users_used

——
marks (спроектировать таблицу)


——
cache_subjects…
как в маине

——
cache_average…
как в маине

——
logs_api
?


——
logs_api3
?

——
logs_sources - запросы к дневникам


——
logs_bot - все сообщения от бота

```