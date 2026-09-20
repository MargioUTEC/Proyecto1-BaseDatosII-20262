"""REST surface consumed by the web client.

Three endpoints, matching the contract the frontend module expects:

    POST /api/query              run one statement, return rows plus cost
    GET  /api/tables             catalog listing with schemas and indexes
    POST /api/tables/reorganize  rebuild the ordered area of a Sequential File
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..bootstrap import build_engine
from ..errors import QueryEngineError
from .schemas import ErrorResponse, QueryRequest, QueryResponse, ReorganizeRequest


def create_app() -> FastAPI:
    app = FastAPI(
        title="CS2042 Query Engine",
        version=__version__,
        description="Parser SQL, planificador de consultas y ejecutor sobre el motor en disco.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = build_engine()

    @app.exception_handler(QueryEngineError)
    async def handle_engine_error(_: Request, exc: QueryEngineError) -> JSONResponse:
        payload = ErrorResponse(
            error=str(exc),
            kind=type(exc).__name__,
            line=getattr(exc, "line", None),
            column=getattr(exc, "column", None),
        )
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=payload.model_dump())

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.post("/api/query", response_model=QueryResponse)
    async def run_query(request: QueryRequest) -> dict:
        return app.state.engine.execute(request.sql).to_dict()

    @app.get("/api/tables")
    async def list_tables() -> dict:
        return {"tables": app.state.engine.tables()}

    @app.post("/api/tables/reorganize")
    async def reorganize(request: ReorganizeRequest) -> dict:
        return app.state.engine.reorganize(request.table)

    return app


app = create_app()
