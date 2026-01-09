import logging
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse


class ValidationError(HTTPException):
    def __init__(self, detail: str = "Not found"):
        super().__init__(status_code=400, detail=detail)
