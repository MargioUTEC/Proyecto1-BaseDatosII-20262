#!/usr/bin/env bash
# Comprobacion rapida de que el sistema entero responde.
#
#   ./scripts/smoke.sh
#
# Verifica los cuatro servicios y las invariantes que importan: que el catalogo
# conteste, que una consulta indexada lea menos bloques que un escaneo, y que
# el costo estimado se parezca al medido. Sale con codigo 1 si algo falla.
set -uo pipefail

cd "$(dirname "$0")/.."
BASE="${DEMO_URL:-http://localhost:8080}"
FALLOS=0

if [ -t 1 ]; then OK=$'\033[32m✓\033[0m'; MAL=$'\033[31m✗\033[0m'; else OK="ok"; MAL="FALLO"; fi

revisar() {
  local nombre="$1" esperado="$2" obtenido="$3"
  if [ "$obtenido" = "$esperado" ]; then
    printf "  %s %-42s %s\n" "$OK" "$nombre" "$obtenido"
  else
    printf "  %s %-42s %s (se esperaba %s)\n" "$MAL" "$nombre" "$obtenido" "$esperado"
    FALLOS=$((FALLOS + 1))
  fi
}

afirmar() {
  local nombre="$1" condicion="$2" detalle="$3"
  if [ "$condicion" = "1" ]; then
    printf "  %s %-42s %s\n" "$OK" "$nombre" "$detalle"
  else
    printf "  %s %-42s %s\n" "$MAL" "$nombre" "$detalle"
    FALLOS=$((FALLOS + 1))
  fi
}

consulta() {
  python3 -c 'import json,sys; print(json.dumps({"sql": sys.argv[1]}))' "$1" \
    | curl -s -XPOST "$BASE/api/query" -H 'Content-Type: application/json' -d @-
}

echo "Servicios"
revisar "consola web"          200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")"
revisar "hoja de estilos"      200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/styles.css")"
revisar "script del cliente"   200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/app.js")"
revisar "proxy /api"           200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/tables")"
revisar "motor"                200 "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health")"
revisar "almacenamiento"       200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/)"
revisar "hashing dinamico"     200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8002/)"

echo
echo "Motor"
revisar "sintaxis invalida devuelve 400" 400 \
  "$(python3 -c 'import json;print(json.dumps({"sql":"SELECT * FORM t"}))' \
     | curl -s -o /dev/null -w '%{http_code}' -XPOST "$BASE/api/query" \
       -H 'Content-Type: application/json' -d @-)"
revisar "tabla inexistente devuelve 400" 400 \
  "$(python3 -c 'import json;print(json.dumps({"sql":"SELECT * FROM no_existe"}))' \
     | curl -s -o /dev/null -w '%{http_code}' -XPOST "$BASE/api/query" \
       -H 'Content-Type: application/json' -d @-)"

TABLA="${SMOKE_TABLA:-customers}"
COLUMNA="${SMOKE_COLUMNA:-Index}"
existe=$(curl -s "$BASE/api/tables" \
  | python3 -c "import json,sys; print(int(any(t['name']=='$TABLA' for t in json.load(sys.stdin)['tables'])))")

if [ "$existe" != "1" ]; then
  echo
  echo "  (sin la tabla '$TABLA' no se comprueban las rutas de acceso;"
  echo "   corre ./scripts/demo.sh primero)"
else
  echo
  echo "Rutas de acceso sobre '$TABLA'"
  lectura=$(consulta "SELECT * FROM $TABLA WHERE \"$COLUMNA\" = 77777" \
    | python3 -c '
import json,sys
d = json.load(sys.stdin)
p = d["plan"]
while "cost" not in p and p.get("children"): p = p["children"][0]
print(p["node"], d["metrics"]["disk_reads"], p["cost"]["estimated_blocks"])')
  read -r ruta leidos estimados <<< "$lectura"

  afirmar "usa un indice para la igualdad" \
    "$(python3 -c "print(int('$ruta' in ('IndexScan','SequentialSearch')))")" "$ruta"
  afirmar "lee pocos bloques" \
    "$(python3 -c "print(int($leidos < 50))")" "$leidos lecturas"
  afirmar "el estimado se parece al medido" \
    "$(python3 -c "print(int(abs($estimados - $leidos) <= max(2, $estimados * 0.25)))")" \
    "estimado $estimados, medido $leidos"
fi

echo
if [ "$FALLOS" -eq 0 ]; then
  echo "Todo en orden."
else
  echo "$FALLOS comprobacion(es) fallaron."
fi
exit $((FALLOS > 0))
