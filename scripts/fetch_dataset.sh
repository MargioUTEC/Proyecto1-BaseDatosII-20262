#!/usr/bin/env bash
# Descarga el dataset de customers de Datablist en data/.
#
# Los CSV pesan entre 17 MB y 400 MB, asi que no viven en el repositorio.
#
#   ./scripts/fetch_dataset.sh                 # 100000 y 500000
#   ./scripts/fetch_dataset.sh 10000 1000000   # tamanos concretos
#
# Escrito para bash 3.2, que es el que trae macOS: sin arreglos asociativos.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data

drive_id() {
  case "$1" in
    100)     echo 1zO8ekHWx9U7mrbx_0Hoxxu6od7uxJqWw ;;
    1000)    echo 1OT84-j5J5z2tHoUvikJtoJFInWmlyYzY ;;
    10000)   echo 1x2IdSNcHGLmot9i1h90gwMJr5lULC2QV ;;
    100000)  echo 1N1xoxgcw2K3d-49tlchXAWw4wuxLj7EV ;;
    500000)  echo 12wwsZMYKTc6scsRDEDp-9IyTQrG1Irwb ;;
    1000000) echo 1NmzWKVP1Yko6VhNE2G4ZO5BMKset00nZ ;;
    2000000) echo 1_zMCoEEcEm0Mp_so3vXSwruhZJj0QIQi ;;
    *)       echo "" ;;
  esac
}

if [ "$#" -eq 0 ]; then
  set -- 100000 500000
fi

for size in "$@"; do
  id=$(drive_id "$size")
  if [ -z "$id" ]; then
    echo "tamano no reconocido: $size" >&2
    echo "disponibles: 100 1000 10000 100000 500000 1000000 2000000" >&2
    exit 1
  fi

  out="data/customers-${size}.csv"
  if [ -s "$out" ]; then
    echo "ya esta: $out"
    continue
  fi

  echo "bajando customers-${size}.csv ..."
  cookies=$(mktemp)
  probe=$(mktemp)
  curl -sSLc "$cookies" \
    "https://drive.usercontent.google.com/download?id=${id}&export=download" -o "$probe"

  if head -c 20 "$probe" | grep -qi '<!DOCTYPE\|<html'; then
    # Drive intercala una pagina de confirmacion para los archivos grandes
    token=$(grep -o 'name="confirm" value="[^"]*"' "$probe" | head -1 | sed 's/.*value="//;s/"//')
    uuid=$(grep -o 'name="uuid" value="[^"]*"' "$probe" | head -1 | sed 's/.*value="//;s/"//')
    curl -sSLb "$cookies" \
      "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=${token}&uuid=${uuid}" \
      -o "$out"
  else
    mv "$probe" "$out"
  fi
  rm -f "$cookies" "$probe"

  if [ ! -s "$out" ]; then
    echo "la descarga quedo vacia: $out" >&2
    exit 1
  fi
  echo "  $(( $(wc -l < "$out") - 1 )) registros"
done
