"""CLI del Bloque C. Ejemplo: python main.py --help"""
import argparse
from pathlib import Path

from benchmark import benchmark
from config import (CHECKPOINT_CADA_S, CLUSTERS_IVF, CONSULTAS_BENCHMARK, DATA_ROOT_OVERRIDE, DESCRIPTORES_POR_IMAGEN,
                    DIR_INDICE, DIR_RESULTADOS, HOST, IMAGENES_ENTRENAMIENTO, K_DEFECTO, LADO_MAX,
                    LIMITE_BUILD, MAX_CANDIDATOS, NFEATURES_SIFT, NPROBE, PALABRAS_CONSULTA,
                    PALABRAS_VISUALES, PUERTO)
from construccion import build
from imagenes import read_image
from indice import VectorIndex
from modelos import ensure_models


# ---------------------------------------------------------------------------
# Bloque para definir los comandos y sus argumentos
# ---------------------------------------------------------------------------
def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bloque C: SFace + SIFT/BoVW + índices en disco")
    sub = p.add_subparsers(dest="action", required=True)

    b = sub.add_parser("build", help="Extraer características y crear índices en disco")
    b.add_argument("--data", type=Path, required=True, help="Carpeta del dataset (puede tener subcarpetas)")
    b.add_argument("--index", type=Path, default=DIR_INDICE)
    b.add_argument("--method", choices=["face", "bovw", "all"], default="all")
    b.add_argument("--limit", type=int, default=LIMITE_BUILD, help="0 = todas las imágenes")
    b.add_argument("--words", type=int, default=PALABRAS_VISUALES, help="Palabras visuales para BoVW")
    b.add_argument("--clusters", type=int, default=CLUSTERS_IVF, help="Centroides IVF facial")
    b.add_argument("--train-images", type=int, default=IMAGENES_ENTRENAMIENTO)
    b.add_argument("--per-image", type=int, default=DESCRIPTORES_POR_IMAGEN)
    b.add_argument("--max-side", type=int, default=LADO_MAX)
    b.add_argument("--nfeatures", type=int, default=NFEATURES_SIFT)
    b.add_argument("--checkpoint-min", type=float, default=CHECKPOINT_CADA_S / 60,
                   help="Cada cuántos minutos guardar el avance del build")

    s = sub.add_parser("search", help="Buscar Top-K por una imagen")
    s.add_argument("--image", type=Path, required=True)
    s.add_argument("--mode", choices=["face", "bovw"], default="face")
    s.add_argument("--k", type=int, default=K_DEFECTO)
    s.add_argument("--nprobe", type=int, default=NPROBE)
    s.add_argument("--query-words", type=int, default=PALABRAS_CONSULTA)
    s.add_argument("--max-candidates", type=int, default=MAX_CANDIDATOS)

    e = sub.add_parser("benchmark", help="Correr experimentos 7 y 8, comparar con full scan")
    e.add_argument("--queries", type=int, default=CONSULTAS_BENCHMARK)
    e.add_argument("--k", type=int, default=K_DEFECTO)
    e.add_argument("--out-dir", type=Path, default=DIR_RESULTADOS)

    v = sub.add_parser("serve", help="Iniciar la API y la interfaz web de búsqueda")
    v.add_argument("--host", default=HOST)
    v.add_argument("--port", type=int, default=PUERTO)

    sub.add_parser("download-models", help="Descargar modelos oficiales OpenCV usados por SFace")
    i = sub.add_parser("info", help="Resumen de índice y dataset")

    for x in (s, e, v, i):
        x.add_argument("--index", type=Path, default=DIR_INDICE)
        x.add_argument("--data-root", type=Path, default=DATA_ROOT_OVERRIDE,
                       help="Si se movió el dataset desde que se indexó")
    return p


# ---------------------------------------------------------------------------
# Bloque para ejecutar cada comando
# ---------------------------------------------------------------------------
def run() -> None:
    args = make_parser().parse_args()
    if args.action == "build":
        build(args.data, args.index, method=args.method, limit=args.limit, words=args.words,
              clusters=args.clusters, train_images=args.train_images, per_image=args.per_image,
              max_side=args.max_side, nfeatures=args.nfeatures,
              checkpoint_cada_s=args.checkpoint_min * 60)
        return
    if args.action == "download-models":
        print("Modelos: ", ensure_models(DIR_INDICE / "models"))
        return

    idx = VectorIndex(args.index, args.data_root)
    if args.action == "info":
        print(f"Imágenes: {idx.meta['count']}, rostros: {idx.meta['n_face']}, "
              f"método: {idx.meta['method']}, origen: {idx.root}")
    elif args.action == "search":
        image = read_image(args.image, idx.meta["max_side"])
        if image is None:
            raise FileNotFoundError(f"Imagen inválida: {args.image}")
        result = idx.search_image(image, args.mode, args.k, nprobe=args.nprobe,
                                  query_words=args.query_words, max_candidates=args.max_candidates)
        print(f"{result['candidates']} candidatos / {result['collection_size']} imágenes "
              f"({result['selectivity'] * 100:.2f}%), {result['total_ms']} ms")
        for n, r in enumerate(result["results"], 1):
            print(f"{n:2}. similitud={r['similarity']:.5f}  id={r['id']}  {r['path']}")
    elif args.action == "benchmark":
        benchmark(idx, args.out_dir, args.queries, args.k)
    else:
        from api import serve  # import tardío: FastAPI solo se necesita al servir
        serve(idx, args.host, args.port)


if __name__ == "__main__":
    run()
