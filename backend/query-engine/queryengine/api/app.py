"""REST surface consumed by the web client.

REST endpoints consumed by the web client, including the spatial map query:

    POST /api/query              run one statement, return rows plus cost
    GET  /api/tables             catalog listing with schemas and indexes
    POST /api/tables/reorganize  rebuild the ordered area of a Sequential File
    POST /api/text/analyze       pipeline lexico (tokens, stopwords, raices) de un texto
    GET  /api/text/index/{name}  estructura fisica de un indice invertido
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..bootstrap import build_engine
from ..errors import QueryEngineError
from .schemas import (
    ErrorResponse,
    LoadRequest,
    QueryRequest,
    QueryResponse,
    ReorganizeRequest,
    SpatialQueryRequest,
    SpatialQueryResponse,
)


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

    @app.post("/api/spatial/query", response_model=SpatialQueryResponse)
    async def run_spatial_query(request: SpatialQueryRequest) -> dict:
        return app.state.engine.spatial_query(
            table=request.table,
            column=request.column,
            operation=request.operation,
            center=request.center,
            radius=request.radius,
            window=request.window,
            include_mbrs=request.include_mbrs,
        )

    @app.post("/api/text/analyze")
    async def analyze_text(request: dict) -> dict:
        from ..textsearch import analyzer

        current = analyzer(request.get("language"))
        text = str(request.get("text", ""))
        return {"analyzer": current.describe(), "steps": current.trace(text),
                "terms": current.tokens(text)}

    @app.get("/api/text/index/{name}")
    async def text_index(name: str) -> dict:
        return app.state.engine.text_index(name)

    from .identificacion import crear_router

    app.include_router(crear_router(app.state.engine))

    @app.get("/api/tables")
    async def list_tables() -> dict:
        return {"tables": app.state.engine.tables()}

    @app.post("/api/tables/load")
    async def load_table(request: LoadRequest) -> dict:
        report = app.state.engine.load(
            request.table,
            request.path,
            header=request.header,
            delimiter=request.delimiter,
            null_token=request.null_token,
            limit=request.limit,
        )
        return report.to_dict()

    @app.post("/api/tables/reorganize")
    async def reorganize(request: ReorganizeRequest) -> dict:
        return app.state.engine.reorganize(request.table)

    return app


app = create_app()
