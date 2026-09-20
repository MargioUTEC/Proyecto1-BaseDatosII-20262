"""Request and response models for the REST surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="Sentencia SQL a ejecutar")


class ReorganizeRequest(BaseModel):
    table: str = Field(..., min_length=1, description="Tabla SEQUENTIAL a reorganizar")


class MetricsModel(BaseModel):
    parse_ms: float
    plan_ms: float
    execution_ms: float
    total_ms: float
    disk_reads: int
    disk_writes: int
    disk_total: int


class QueryResponse(BaseModel):
    statement: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    affected_rows: int
    message: str
    plan: dict | None
    plan_text: str
    metrics: MetricsModel


class ErrorResponse(BaseModel):
    error: str
    kind: str
    line: int | None = None
    column: int | None = None
