#!/usr/bin/env bash
# Descarga el dataset de customers de Datablist en data/.
#
# Los CSV pesan entre 17 MB y 400 MB, asi que no viven en el repositorio.
# Uso:  ./scripts/fetch_dataset.sh [100000] [500000] ...
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data

declare -A IDS=(
  [100]=1zO8ekHWx9U7mrbx_0Hoxxu6od7uxJqWw
  [1000]=1OT84-j5J5z2tHoUvikJtoJFInWmlyYzY
  [10000]=1x2IdSNcHGLmot9i1h90gwMJr5lULC2QV
  [100000]=1N1xoxgcw2K3d-49tlchXAWw4wuxLj7EV
  [500000]=12wwsZMYKTc6scsRDEDp-9IyTQrG1Irwb
  [1000000]=1NmzWKVP1Yko6VhNE2G4ZO5BMKset00nZ
  [2000000]=1_zMCoEEcEm0Mp_so3vXSwruhZJj0QIQi
)

sizes=("${@:-100000 500000}")
[ $# -eq 0 ] && sizes=(100000 500000)

for size in "${sizes[@]}"; do
  id="${IDS[$size]:-}"
  if [ -z "$id" ]; then
    echo "tamano no reconocido: $size (usa ${!IDS[*]})" >&2
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
  curl -sSLc "$cookies" "https://drive.usercontent.google.com/download?id=${id}&export=download" -o "$probe"
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
  echo "  $(wc -l < "$out") lineas"
done
