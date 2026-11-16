"""Levanta el motor y el cliente en un solo origen, sin Docker.

En produccion nginx enruta /api/ al motor y sirve el frontend; en desarrollo
eso obligaba a dos servidores y el navegador bloqueaba las peticiones por CORS.
Aqui el mismo proceso monta las dos cosas, que es lo que hace que la pantalla de
identificacion funcione abriendo una sola URL.

    python scripts/servir_local.py
    python scripts/servir_local.py --backend memory --puerto 8001
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puerto", default=8001, type=int)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--backend", default="disk", choices=["disk", "memory"])
    parser.add_argument("--vectorial", default="http://127.0.0.1:8003")
    opciones = parser.parse_args()

    os.environ["QE_STORAGE_BACKEND"] = opciones.backend
    os.environ["QE_VECTORIAL_URL"] = opciones.vectorial
    os.environ.setdefault("QE_DATA_DIR", str(RAIZ / "data"))
    os.environ.setdefault("QE_TABLE_DIR", str(RAIZ / "data" / "tables"))
    if opciones.backend == "disk":
        os.environ.setdefault("QE_CATALOG_PATH", str(RAIZ / "data" / "catalog.json"))
    else:
        os.environ.setdefault("QE_CATALOG_PATH", "none")

    sys.path.insert(0, str(RAIZ / "backend" / "query-engine"))
    sys.path.insert(0, str(RAIZ))

    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from queryengine.api.app import create_app

    app = create_app()
    app.mount("/", StaticFiles(directory=str(RAIZ / "frontend"), html=True), name="cliente")

    print(f"motor en http://{opciones.host}:{opciones.puerto}  (backend {opciones.backend})")
    print(f"  consola SQL     http://{opciones.host}:{opciones.puerto}/index.html")
    print(f"  identificacion  http://{opciones.host}:{opciones.puerto}/identificacion.html")
    uvicorn.run(app, host=opciones.host, port=opciones.puerto, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
