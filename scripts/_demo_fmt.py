"""Formatea la respuesta de /api/query para la demostracion en consola."""

import json
import sys

VERDE = "\033[36m"
AMBAR = "\033[33m"
TENUE = "\033[2m"
NORMAL = "\033[0m"


def costo(nodo):
    while nodo and "cost" not in nodo:
        hijos = nodo.get("children") or []
        nodo = hijos[0] if hijos else None
    return nodo["cost"] if nodo else None


def main() -> int:
    datos = json.load(sys.stdin)
    if "error" in datos:
        print(f"  {AMBAR}error:{NORMAL} {datos['error']}")
        return 1

    if datos.get("message"):
        print(f"  {datos['message']}")

    columnas = datos.get("columns") or []
    filas = datos.get("rows") or []
    if columnas:
        anchos = [
            min(26, max(len(c), *(len(str(f[i])) for f in filas[:6])) if filas else len(c))
            for i, c in enumerate(columnas)
        ]
        print("  " + "  ".join(c[:w].ljust(w) for c, w in zip(columnas, anchos, strict=True)))
        print("  " + "  ".join("-" * w for w in anchos))
        for fila in filas[:6]:
            print("  " + "  ".join(
                str(v)[:w].ljust(w) for v, w in zip(fila, anchos, strict=True)))
        if len(filas) > 6:
            print(f"  {TENUE}… {len(filas) - 6:,} filas mas{NORMAL}")
        print(f"  {TENUE}{len(filas):,} fila(s){NORMAL}")

    plan = datos.get("plan") or {}
    nodo = plan
    while nodo.get("children"):
        nodo = nodo["children"][0]
    estimado = costo(plan)
    metricas = datos.get("metrics", {})
    medido = metricas.get("disk_reads", 0) + metricas.get("disk_writes", 0)

    linea = f"  {AMBAR}{nodo.get('node', '?')}{NORMAL}"
    if estimado:
        linea += f"   estimado {estimado['estimated_blocks']:>9} bloques"
    linea += f"   medido {medido:>9}"
    if estimado and abs(medido - estimado["estimated_blocks"]) < 0.5:
        linea += f" {VERDE}clavado{NORMAL}"
    print(linea)
    print(
        f"  {TENUE}{metricas.get('disk_reads', 0):,} lecturas · "
        f"{metricas.get('disk_writes', 0):,} escrituras · "
        f"{metricas.get('total_ms', 0):.2f} ms{NORMAL}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
