# Build context is the repository root, so the physical storage module can be
# copied in alongside the engine that binds to it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QE_CATALOG_PATH=/var/lib/cs2042/catalog.json \
    QE_TABLE_DIR=/var/lib/cs2042/tables \
    QE_BLK01_PATH=/app/storage

WORKDIR /app

COPY backend/query-engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/query-engine/queryengine ./queryengine
COPY storage/*.py ./storage/
COPY hashing_dinamico/*.py ./hashing_dinamico/

RUN mkdir -p /var/lib/cs2042 /data \
    && useradd --create-home --uid 1000 engine \
    && chown -R engine /var/lib/cs2042 /data /app
USER engine

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

CMD ["uvicorn", "queryengine.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
