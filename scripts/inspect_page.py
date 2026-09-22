#!/usr/bin/env python3
"""Abre un archivo binario de tabla y muestra lo que hay dentro de una pagina.

Sirve para la demostracion: enseña que los datos viven en bloques de tamaño
fijo con cabecera, directorio de slots y registros empaquetados, no en un
formato de alto nivel.

    python scripts/inspect_page.py --list
    python scripts/inspect_page.py customers --page 0
    python scripts/inspect_page.py customers --page 0 --hex
    python scripts/inspect_page.py customers --rid 12,3
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend", "query-engine"))

from queryengine.catalog import Catalog  # noqa: E402
from queryengine.storage.blk01 import load  # noqa: E402
from queryengine.storage.codec import RecordCodec  # noqa: E402

CATALOGO = os.environ.get("QE_CATALOG_PATH", os.path.join(ROOT, "data", "run", "catalog.json"))
TABLAS = os.environ.get("QE_TABLE_DIR", os.path.join(ROOT, "data", "run", "tables"))


def volcado_hex(datos: bytes, desde: int = 0, cuantos: int = 128, ancho: int = 16) -> str:
    lineas = []
    for base in range(desde, min(desde + cuantos, len(datos)), ancho):
        trozo = datos[base : base + ancho]
        hexa = " ".join(f"{b:02x}" for b in trozo)
        texto = "".join(chr(b) if 32 <= b < 127 else "." for b in trozo)
        lineas.append(f"  {base:06x}  {hexa:<{ancho * 3}} |{texto}|")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspector de paginas binarias")
    parser.add_argument("tabla", nargs="?", help="nombre de la tabla")
    parser.add_argument("--page", type=int, default=0, help="numero de pagina (0 es la primera)")
    parser.add_argument("--rid", help="localizar un RID concreto, como 12,3")
    parser.add_argument("--hex", action="store_true", help="volcado hexadecimal del bloque")
    parser.add_argument("--rows", type=int, default=4, help="registros a decodificar")
    parser.add_argument("--overflow", action="store_true", help="leer el area de desbordamiento")
    parser.add_argument("--list", action="store_true", help="listar las tablas disponibles")
    args = parser.parse_args(argv)

    catalogo = Catalog(CATALOGO)
    capa = load()

    if args.list or not args.tabla:
        print(f"catalogo : {CATALOGO}")
        print(f"tablas   : {TABLAS}\n")
        for esquema in catalogo.tables():
            for sufijo, etiqueta in ((".bin", "principal"), (".ovf", "overflow")):
                ruta = os.path.join(TABLAS, f"{esquema.name.lower()}{sufijo}")
                if os.path.exists(ruta):
                    tamano = os.path.getsize(ruta)
                    print(
                        f"  {esquema.name:<16} {etiqueta:<10} {tamano:>12,} B  "
                        f"{tamano // esquema.page_size:>6} paginas de {esquema.page_size} B"
                    )
        return 0

    esquema = catalogo.table(args.tabla)
    codec = RecordCodec(esquema)
    sufijo = ".ovf" if args.overflow else ".bin"
    ruta = os.path.join(TABLAS, f"{esquema.name.lower()}{sufijo}")
    if not os.path.exists(ruta):
        print(f"no existe {ruta}", file=sys.stderr)
        return 1

    pagina = args.page
    slot_buscado = None
    if args.rid:
        pagina, slot_buscado = (int(parte) for parte in args.rid.split(","))

    tamano = os.path.getsize(ruta)
    total = tamano // esquema.page_size
    if pagina >= total:
        print(f"la pagina {pagina} no existe; el archivo tiene {total}", file=sys.stderr)
        return 1

    # Lectura directa con seek, igual que la hace el motor
    with open(ruta, "rb") as archivo:
        archivo.seek(pagina * esquema.page_size)
        bloque = archivo.read(esquema.page_size)

    print(f"archivo   {ruta}")
    print(f"          {tamano:,} bytes = {total:,} paginas de {esquema.page_size} B")
    print(f"esquema   {esquema.record_format}  ->  {codec.size} B por registro almacenado")
    print(f"          {esquema.records_per_page} registros por pagina\n")

    cab = struct.unpack_from("<iiiii", bloque, 0)
    print(f"CABECERA DE LA PAGINA {pagina}   ({capa.header_size} bytes)")
    campos = ("page_id", "record_count", "free_space_offset", "next_page_id", "prev_page_id")
    for nombre, valor in zip(campos, cab, strict=True):
        print(f"  {nombre:<20} {valor}")
    libres = cab[2] - (capa.header_size + cab[1] * capa.slot_size)
    print(f"  {'espacio libre':<20} {libres} bytes\n")

    print(f"DIRECTORIO DE SLOTS   ({capa.slot_size} bytes por entrada)")
    vivos = []
    for slot in range(cab[1]):
        posicion = capa.header_size + slot * capa.slot_size
        desplazamiento, largo = struct.unpack_from(capa.slot_format, bloque, posicion)
        estado = "libre (borrado)" if largo == 0 else f"offset {desplazamiento}, {largo} B"
        marca = "  <-- buscado" if slot == slot_buscado else ""
        if slot < 12 or slot == slot_buscado:
            print(f"  slot {slot:<4} {estado}{marca}")
        if largo:
            vivos.append((slot, desplazamiento, largo))
    if cab[1] > 12:
        print(f"  … {cab[1] - 12} slots mas")
    print(f"  {len(vivos)} vivos de {cab[1]}\n")

    objetivo = [t for t in vivos if t[0] == slot_buscado] if slot_buscado is not None else vivos
    print("REGISTROS DECODIFICADOS")
    if not objetivo:
        print("  (ninguno)")
    for slot, desplazamiento, largo in objetivo[: args.rows]:
        crudo = bloque[desplazamiento : desplazamiento + largo]
        valores = codec.unpack(crudo)
        print(f"  RID ({pagina}, {slot})")
        for columna, valor in zip(esquema.columns, valores, strict=False):
            texto = "NULL" if valor is None else str(valor)
            print(f"      {columna.name:<20} {texto[:56]}")
        if args.hex:
            print("    bytes en crudo:")
            print(volcado_hex(crudo, 0, min(largo, 96)))
        print()

    if args.hex:
        print("PRIMEROS BYTES DEL BLOQUE")
        print(volcado_hex(bloque, 0, 160))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
