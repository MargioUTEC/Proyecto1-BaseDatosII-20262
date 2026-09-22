#!/usr/bin/env bash
# Demostracion guiada del motor, pensada para grabarse.
#
#   ./scripts/demo.sh            # corre los pasos seguidos
#   ./scripts/demo.sh --pausa    # espera Enter entre pasos, para narrar
#
# Requiere los servicios levantados (docker compose up) y el dataset en data/.
set -euo pipefail

cd "$(dirname "$0")/.."

BASE="${DEMO_URL:-http://localhost:8080}"
CSV="${DEMO_CSV:-customers-100000.csv}"
PAUSA=0
[ "${1:-}" = "--pausa" ] && PAUSA=1

if [ -t 1 ]; then
  N=$'\033[0m'; B=$'\033[1m'; TEN=$'\033[2m'; AMB=$'\033[33m'; VER=$'\033[36m'
else
  N=""; B=""; TEN=""; AMB=""; VER=""
fi

paso() {
  echo
  echo "${B}━━━ $* ${N}"
  echo
  [ "$PAUSA" = "1" ] && { printf "${TEN}   [Enter para continuar]${N}"; read -r _; }
  return 0
}

sql() {
  local consulta="$1"
  echo "${VER}${consulta}${N}"
  local cuerpo
  cuerpo=$(python3 -c 'import json,sys; print(json.dumps({"sql": sys.argv[1]}))' "$consulta")
  curl -s -XPOST "$BASE/api/query" -H 'Content-Type: application/json' -d "$cuerpo" \
    | python3 scripts/_demo_fmt.py
}

esperar() {
  for _ in $(seq 1 60); do
    curl -sf "$BASE/api/tables" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "No responde $BASE. ¿Corriste 'docker compose up'?" >&2
  exit 1
}

echo "${B}Consola SQL — CS2042${N}"
echo "${TEN}motor en $BASE · dataset $CSV${N}"
esperar

paso "1. Estado inicial: el catalogo esta vacio"
curl -s "$BASE/api/tables" | python3 -c '
import json,sys
t = json.load(sys.stdin)["tables"]
print("  tablas:", len(t) or "ninguna")
for x in t: print("   ", x["name"], x["storage"], format(x["row_count"], ","), "filas")'

paso "2. Crear la tabla. El formato binario sale de los tipos declarados"
sql 'CREATE TABLE customers ("Index" INT PRIMARY KEY, "Customer Id" CHAR(15), "First Name" CHAR(16), "Last Name" CHAR(16), "Company" CHAR(40), "City" CHAR(28), "Country" CHAR(56), "Phone 1" CHAR(24), "Phone 2" CHAR(24), "Email" CHAR(48), "Subscription Date" DATE, "Website" CHAR(44)) USING HEAP'

paso "3. Cargar el CSV. No pasa por el parser: una fila del archivo es una fila de la tabla"
curl -s -XPOST "$BASE/api/tables/load" -H 'Content-Type: application/json' \
  -d "{\"table\":\"customers\",\"path\":\"$CSV\"}" \
  | python3 -c '
import json,sys
d = json.load(sys.stdin)
if "error" in d: print("  error:", d["error"]); sys.exit(1)
m = d["metrics"]
print("  %s filas en %.2f s  (%s filas/s)" % (
    format(d["rows_inserted"], ","), m["execution_ms"]/1000, format(int(d["rows_per_second"]), ",")))
print("  %s escrituras — una por pagina llena, no una por registro" % format(m["disk_writes"], ","))'

paso "4. Busqueda puntual SIN indice: hay que leer la tabla entera"
sql 'SELECT "First Name", "Last Name", "Country" FROM customers WHERE "Index" = 77777'

paso "5. Crear los dos indices en disco"
sql 'CREATE INDEX ix_btree ON customers("Index") USING BTREE'
sql 'CREATE INDEX ix_hash ON customers("Index") USING HASH'

paso "6. La MISMA consulta, ahora con indice"
sql 'SELECT "First Name", "Last Name", "Country" FROM customers WHERE "Index" = 77777'

paso "7. Rango angosto: el planificador baja por el arbol y barre las hojas"
sql 'SELECT "Index", "City" FROM customers WHERE "Index" BETWEEN 5000 AND 5010'

paso "8. Rango amplio: el indice deja de convenir y vuelve al escaneo completo"
sql 'SELECT "Index" FROM customers WHERE "Index" >= 1 AND "Index" <= 60000'

paso "9. EXPLAIN: planificar sin ejecutar"
sql 'EXPLAIN SELECT * FROM customers WHERE "Index" = 42'

paso "10. Los datos estan en bloques binarios, no en un formato de alto nivel"
echo "${TEN}  scripts/inspect_page.py customers --page 0${N}"
echo "${TEN}  (necesita el catalogo y las tablas en local; ver el README)${N}"

echo
echo "${B}Listo.${N} La consola queda en ${AMB}$BASE${N}"
