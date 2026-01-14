import logging
import traceback

from fastapi import APIRouter, HTTPException, Depends, Request

from app.state import state
from app.models.request import SaveRequest, GetRequest, QueryRequest, HistoryRequest, GroupRequest
from app.models.response import SaveResponse, QueryResponse, HistoryResponse, GroupResponse
from app.services.read_service import ReadService
from app.services.write_service import WriteService

router = APIRouter()


@router.post("/write/save")
async def save_endpoint(req: SaveRequest) -> SaveResponse:
    try:
        changed = await WriteService.save(
            req.table, req.entity, req.data, req.now
        )
    except Exception as e:
        logging.error(f"SAVE {e} {req}")
        raise e

    return SaveResponse(success=True, changed=changed)


@router.post("/read/get")
async def get_endpoint(req: GetRequest) -> dict | None:
    record = await ReadService.get_current(
        req.table, req.entity
    )

    if not record:
        raise HTTPException(status_code=404, detail="Not found")
    return record


@router.post("/read/query")
async def query_endpoint(req: QueryRequest) -> QueryResponse:
    records, total = await ReadService.query(
        req.table, req.fields, req.filters,
        req.limit, req.offset, req.sort_by
    )

    return QueryResponse(results=records, count=len(records), total=total)


@router.post("/read/history")
async def history_endpoint(req: HistoryRequest) -> HistoryResponse:
    history, total = await ReadService.get_history(
        req.table, req.fields, req.filters,
        req.limit, req.offset, req.sort_by
    )

    return HistoryResponse(history=history, count=len(history), total=total)


@router.post("/read/group")
async def group_endpoint(req: GroupRequest) -> GroupResponse:
    results, total = await ReadService.get_grouped(
        req.table, req.is_history, req.fields, req.filters,
        req.group_by, req.aggs, req.having,
        req.limit, req.offset, req.sort_by
    )

    return GroupResponse(results=results, count=len(results), total=total)


@router.get("/config/tables")
async def get_tables_config(request: Request) -> dict:
    config = {}
    for name, table_binding in state.TABLES_CONFIG.items():
        schema = table_binding.schema
        allowed = "/read/get" in request.state.key_info.get("_", []) or \
                  "/read/get" in request.state.key_info.get(name, []) or \
                  request.state.key_info.get("_", []) == ["*"] or \
                  request.state.key_info.get(name, []) == ["*"]

        if allowed:
            write_mode = schema.write_mode

            config[name] = {
                "name": schema.name,
                "entity_fields": [{"name": f.name, "type": f.type.value, "size": f.size, "nullable": f.nullable}
                                  for f in schema.entity_fields],
                "search_fields": [{"name": f.name, "type": f.type.value, "size": f.size, "nullable": f.nullable}
                                  for f in schema.search_fields],
                "data_fields": [{"name": f.name, "type": f.type.value, "size": f.size, "nullable": f.nullable}
                                for f in schema.data_fields],
                "indexes": schema.indexes,

                "write_mode": write_mode,

                "current": table_binding.has_current(),
                "history": table_binding.has_history(),

                "database": table_binding.database,
                "history_database": table_binding.history_database,

                "field_change_count": schema.field_change_count,
                "field_used_count": schema.field_used_count,

                "touch_updated_at": schema.touch_updated_at
            }
            if schema.field_change_count:
                config[name]['field_change_count_name'] = schema.field_change_count_name
            if schema.field_used_count:
                config[name]['field_used_count_name'] = schema.field_used_count_name
    return config


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}
