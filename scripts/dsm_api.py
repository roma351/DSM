import datetime
from typing import Optional, Literal
import httpx
import os


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


class HTTPError(Exception):
    def __init__(self, message="A custom error occurred", status_code=0):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class DSM:
    def __init__(self, url: str = None, key: str = None):
        self.url = url
        self.key = key
        self.client = httpx.AsyncClient(headers={"X-API-KEY": key})

    async def save(
            self,
            table: str,
            entity_id: str,
            data: dict,
            now: Optional[datetime.datetime] = None,
            entity_type: Optional[str] = None
    ):
        if now is None:
            now = datetime.datetime.now()

        return await self._post("/write/save", {
            "table": table,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "data": data,
            "now": _ensure_utc(now).strftime("%Y-%m-%d %H:%M:%S.%f")
        })

    async def get(
            self,
            table: str,
            entity_id: str,
            entity_type: Optional[str] = None
    ):
        return await self._post("/read/get", {
            "table": table,
            "entity_id": entity_id,
            "entity_type": entity_type
        })

    async def query(
            self,
            table: str,
            filters: Optional[dict] = {},
            entity_type: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
            sort: Literal["asc", "desc"] = "asc",
            at_date: Optional[str] = None
    ):
        return await self._post("/read/query", {
            "table": table,
            "filters": filters,
            "entity_type": entity_type,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "at_date": at_date
        })

    async def history(
            self,
            table: str,
            entity_id: str | list[str],
            entity_type: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
            sort: Literal["asc", "desc"] = "asc",
            from_date: Optional[str] = None,
            to_date: Optional[str] = None
    ):
        return await self._post("/read/history", {
            "table": table,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "from_date": from_date,
            "to_date": to_date
        })

    async def _get(self, path: str, params: dict = None) -> dict:
        return await self.request("GET", path, params=params)

    async def _post(self, path: str, json2: dict = None) -> dict:
        return await self.request("POST", path, json2=json2)

    async def request(self, method: str, path: str, params: dict = None, json2: dict = None) -> dict:
        response = await self.client.request(method, f"{self.url}{path}", params=params, json=json2)
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPError(message=response.text, status_code=response.status_code)


from dotenv import load_dotenv
load_dotenv()

DSMClient = DSM(url=os.getenv("DSM_API"), key=os.getenv("DSM_KEY"))
