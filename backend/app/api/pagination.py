"""Pagination for list endpoints: `limit` and `offset` in, `{items, total, limit, offset}` out."""

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams:
    def __init__(self, limit: int = Query(50, ge=1, le=MAX_LIMIT, description="Page size"), offset: int = Query(0, ge=0, description="Rows to skip")) -> None:
        self.limit = limit
        self.offset = offset
