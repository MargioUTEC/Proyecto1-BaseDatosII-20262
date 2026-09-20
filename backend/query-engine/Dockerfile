FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QE_CATALOG_PATH=/data/catalog.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY queryengine ./queryengine

RUN mkdir -p /data && useradd --create-home --uid 1000 engine && chown -R engine /data /app
USER engine

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

CMD ["uvicorn", "queryengine.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
