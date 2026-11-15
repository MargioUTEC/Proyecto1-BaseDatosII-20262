FROM python:3.11-slim

WORKDIR /app/vectorial
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# El índice y el dataset se montan como volúmenes (ver README).
ENV VECTORIAL_INDEX=/data/indices_c \
    VECTORIAL_DATA_ROOT=/data/lfw \
    VECTORIAL_RESULTS=/results
EXPOSE 8003
CMD ["python", "main.py", "serve", "--host", "0.0.0.0"]
