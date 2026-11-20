"""Request and response models for the REST surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="Sentencia SQL a ejecutar")


class ReorganizeRequest(BaseModel):
    table: str = Field(..., min_length=1, description="Tabla SEQUENTIAL a reorganizar")


class LoadRequest(BaseModel):
    table: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1, description="Ruta relativa a QE_DATA_DIR")
    header: bool = True
    delimiter: str = Field(",", min_length=1, max_length=1)
    null_token: str = ""
    limit: int | None = Field(None, gt=0)


class SpatialQueryRequest(BaseModel):
    table: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    operation: Literal["radius", "window"]
    center: tuple[float, float] | None = None
    radius: float | None = Field(None, ge=0)
    window: tuple[float, float, float, float] | None = None
    include_mbrs: bool = False

    @model_validator(mode="after")
    def validate_operation_arguments(self):
        if self.operation == "radius" and (self.center is None or self.radius is None):
            raise ValueError("radius requiere center y radius")
        if self.operation == "window" and self.window is None:
            raise ValueError("window requiere window=[xmin,ymin,xmax,ymax]")
        if self.window is not None and (self.window[0] > self.window[2] or self.window[1] > self.window[3]):
            raise ValueError("window requiere xmin <= xmax e ymin <= ymax")
        return self


class MetricsModel(BaseModel):
    parse_ms: float
    plan_ms: float
    execution_ms: float
    total_ms: float
    disk_reads: int
    disk_writes: int
    disk_total: int


class SpatialMetricsModel(MetricsModel):
    index_reads: int
    index_writes: int
    table_reads: int
    table_writes: int
    index_ms: float
    table_ms: float


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


class SpatialQueryResponse(BaseModel):
    table: str
    column: str
    operation: Literal["radius", "window"]
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    points: list[tuple[float, float]]
    query_geometry: dict
    mbrs: list[tuple[float, float, float, float]] | None
    plan: dict
    metrics: SpatialMetricsModel


class ErrorResponse(BaseModel):
    error: str
    kind: str
    line: int | None = None
    column: int | None = None
