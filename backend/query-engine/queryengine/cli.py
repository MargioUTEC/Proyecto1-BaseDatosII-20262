"""Interactive SQL client for the engine.

Useful for driving the engine without the web frontend: loading a dataset,
checking a plan, or recording the demo. Statements end with a semicolon and may
span several lines; backslash commands are borrowed from psql because everyone
already knows them.
"""

from __future__ import annotations

import argparse
import sys

from .bootstrap import Settings, build_engine
from .engine import QueryEngine, QueryResult
from .errors import QueryEngineError

BANNER = """CS2042 query engine.  \\? para la ayuda, \\q para salir.
Las sentencias terminan en ';' y pueden ocupar varias lineas."""

HELP = """\\d              lista las tablas
\\d NOMBRE       describe una tabla
\\i ARCHIVO      ejecuta un archivo .sql
\\timing         muestra u oculta las metricas
\\q              salir"""

MAX_COLUMN_WIDTH = 40


def render(result: QueryResult) -> str:
    if not result.columns:
        return result.message
    rows = [[_cell(value) for value in row] for row in result.rows]
    widths = []
    for position, name in enumerate(result.columns):
        longest = max((len(row[position]) for row in rows), default=0)
        widths.append(min(MAX_COLUMN_WIDTH, max(len(name), longest)))
    heading = zip(result.columns, widths, strict=True)
    lines = [
        " | ".join(name[:width].ljust(width) for name, width in heading),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(
        " | ".join(value[:width].ljust(width) for value, width in zip(row, widths, strict=False))
        for row in rows
    )
    lines.append(f"({result.row_count} fila{'s' if result.row_count != 1 else ''})")
    return "\n".join(lines)


def render_metrics(result: QueryResult) -> str:
    metrics = result.metrics
    return (
        f"  lecturas {metrics.disk_reads}  escrituras {metrics.disk_writes}"
        f"  |  parse {metrics.parse_ms:.2f} ms  plan {metrics.plan_ms:.2f} ms"
        f"  ejecucion {metrics.execution_ms:.2f} ms"
    )


def _cell(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class Console:
    def __init__(self, engine: QueryEngine, timing: bool = True, show_plan: bool = False):
        self.engine = engine
        self.timing = timing
        self.show_plan = show_plan

    def run_statement(self, sql: str) -> bool:
        try:
            result = self.engine.execute(sql)
        except QueryEngineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return False
        print(render(result))
        if self.show_plan and result.plan_text:
            print(result.plan_text)
        if self.timing:
            print(render_metrics(result))
        return True

    def run_file(self, path: str, stop_on_error: bool = True) -> bool:
        """Run a script. Stops at the first failed statement by default.

        Carrying on would leave the rest of the script running against a table
        that was never populated, printing empty results that read like success
        while the real error scrolled past.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                script = handle.read()
        except OSError as exc:
            print(f"error: no se pudo leer '{path}': {exc}", file=sys.stderr)
            return False
        ok = True
        for statement in _split_statements(script):
            print(f"-- {statement.splitlines()[0][:70]}")
            if not self.run_statement(statement):
                ok = False
                if stop_on_error:
                    print("se detuvo en la sentencia anterior", file=sys.stderr)
                    return False
        return ok

    def command(self, line: str) -> bool:
        parts = line.split(maxsplit=1)
        name = parts[0]
        argument = parts[1].strip() if len(parts) > 1 else ""
        if name in ("\\q", "\\quit"):
            return False
        if name == "\\?":
            print(HELP)
        elif name == "\\d":
            self._describe(argument)
        elif name == "\\i":
            self.run_file(argument, stop_on_error=False)
        elif name == "\\timing":
            self.timing = not self.timing
            print(f"metricas {'activadas' if self.timing else 'desactivadas'}")
        else:
            print(f"comando desconocido: {name}. \\? para la ayuda", file=sys.stderr)
        return True

    def _describe(self, name: str) -> None:
        tables = self.engine.tables()
        if not name:
            if not tables:
                print("no hay tablas")
                return
            for table in tables:
                indexes = ", ".join(
                    f"{index['name']}({index['column']}, {index['kind']})"
                    for index in table["indexes"]
                ) or "sin indices"
                print(
                    f"{table['name']:<24} {table['storage']:<11} "
                    f"{table['row_count']:>9} filas  {table['page_count']:>6} paginas  {indexes}"
                )
            return
        table = next((item for item in tables if item["name"].lower() == name.lower()), None)
        if table is None:
            print(f"la tabla '{name}' no existe", file=sys.stderr)
            return
        print(f"Tabla {table['name']}  motor {table['storage']}")
        print(
            f"  pagina {table['page_size']} B, registro {table['stored_record_size']} B "
            f"({table['record_format']}), {table['records_per_page']} registros por pagina"
        )
        for column in table["columns"]:
            flags = " PRIMARY KEY" if column["primary_key"] else ""
            flags += "" if column["nullable"] else " NOT NULL"
            print(f"  {column['name']:<22} {column['type']:<14}{flags}")
        for index in table["indexes"]:
            print(f"  indice {index['name']} sobre ({index['column']}) usando {index['kind']}")

    def repl(self) -> None:
        print(BANNER)
        buffer: list[str] = []
        while True:
            prompt = "sql> " if not buffer else "  ...> "
            try:
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print()
                return
            stripped = line.strip()
            if not stripped and not buffer:
                continue
            if not buffer and stripped.startswith("\\"):
                if not self.command(stripped):
                    return
                continue
            buffer.append(line)
            if stripped.endswith(";"):
                self.run_statement("\n".join(buffer))
                buffer.clear()


def _split_statements(script: str) -> list[str]:
    """Split on semicolons that are not inside a quoted literal."""
    statements, current, in_string = [], [], False
    for char in script:
        if char == "'":
            in_string = not in_string
        if char == ";" and not in_string:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="queryengine", description="Cliente SQL del motor")
    parser.add_argument("-c", "--execute", metavar="SQL", help="ejecuta una sentencia y termina")
    parser.add_argument("-f", "--file", metavar="ARCHIVO", help="ejecuta un archivo .sql y termina")
    parser.add_argument("--backend", default=None, help="memory o disk")
    parser.add_argument("--catalog", default=None, help="ruta del catalogo")
    parser.add_argument("--data-dir", default=None, help="directorio permitido para COPY")
    parser.add_argument("--plan", action="store_true", help="muestra el plan de cada consulta")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="con -f, sigue con la siguiente sentencia tras un error",
    )
    parser.add_argument("--no-timing", action="store_true", help="oculta las metricas")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.backend:
        settings = Settings(
            backend=args.backend,
            catalog_path=args.catalog or settings.catalog_path,
            data_dir=args.data_dir or settings.data_dir,
            table_dir=settings.table_dir,
            physical_path=settings.physical_path,
        )
    elif args.catalog or args.data_dir:
        settings = Settings(
            backend=settings.backend,
            catalog_path=args.catalog or settings.catalog_path,
            data_dir=args.data_dir or settings.data_dir,
            table_dir=settings.table_dir,
            physical_path=settings.physical_path,
        )

    try:
        engine = build_engine(settings)
    except (QueryEngineError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    console = Console(engine, timing=not args.no_timing, show_plan=args.plan)
    if args.execute:
        return 0 if console.run_statement(args.execute) else 1
    if args.file:
        return 0 if console.run_file(args.file, not args.continue_on_error) else 1
    console.repl()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
