"""
Command-line interface: `serve` (the web UI, default), `index` (build the optional
SQLite FTS5 full-text engine) and `vectorize` (build the optional semantic store).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from .bibl import DEFAULT_BIBL_DB, DEFAULT_BIBL_XML, build_bibl_cache
from .corpus import DEFAULT_DB, DEFAULT_DTS_BASE, DEFAULT_ROOT
from .search import DEFAULT_TIMEOUT, build_index
from .vectors import (DEFAULT_BATCH_SIZE, DEFAULT_MODEL_DIR, DEFAULT_MODEL_ID,
                      DEFAULT_VECTORS_DB, build_vectors, ensure_model)
from .web import create_app

__doc__ = """\
browse the CLLG TEI corpus as HTML excerpts.

    browse.py serve     [--root data] [--host 127.0.0.1] [--port 5000]
                        [--fulltext --db var/search.sqlite] [--dts-base URL]
                        [--vectors --vectors-db var/vectors/vectors.sqlite]
    browse.py index     [--root data] [--db var/search.sqlite]
                        [--jsonl PATH] [-j JOBS] [--timeout SECONDS]
    browse.py bibl      [--bibl-xml bibl.xml] [--bibl-db var/bibl.sqlite]
    browse.py vectorize [--root data] [--vectors-db var/vectors/vectors.sqlite]
                        [--model HF_ID] [--model-dir DIR] [-j JOBS]
                        [--timeout SECONDS] [--batch-size N] [--device auto|cpu|cuda]
                        [--from-fts var/search.sqlite]

`serve` launches the web UI (default).  Its home page is organised Author -> Work
and the search box finds an author or work to navigate to (always on, no index).

Full-text search over passage *content* is an optional add-on: build the index
once with `index`, then start the server with `--fulltext`.

Semantic "Similar passages" is a second optional add-on: build the vector store
once with `vectorize` (needs the build-only ML deps in requirements-vectors.txt),
then start the server with `--vectors`.  Serving needs only `sqliteai-vector`.

If you also build the FTS index, `vectorize --from-fts var/search.sqlite` reuses
its already-rendered leaf text and skips re-rendering the corpus with Saxon.
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="launch the web browser UI (default)")
    p_serve.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.add_argument("--fulltext", action="store_true",
                         help="also enable optional full-text content search "
                              "(requires an index built by `browse.py index`)")
    p_serve.add_argument("--db", type=Path, default=DEFAULT_DB,
                         help="SQLite FTS5 search index used by --fulltext")
    p_serve.add_argument("--dts-base", default=DEFAULT_DTS_BASE,
                         help="base URL of a DTS server for the reading-page link")
    p_serve.add_argument("--vectors", action="store_true",
                         help="also enable optional semantic 'Similar passages' "
                              "(requires a store built by `browse.py vectorize`)")
    p_serve.add_argument("--vectors-db", type=Path, default=DEFAULT_VECTORS_DB,
                         help="SQLite vector store used by --vectors")
    p_serve.add_argument("--bibl-db", type=Path, default=DEFAULT_BIBL_DB,
                         help="SQLite bibliographic cache (built by `browse.py bibl`)")
    p_serve.add_argument("--debug", action="store_true")

    p_bibl = sub.add_parser(
        "bibl", help="(optional) build the SQLite bibliographic cache from bibl.xml")
    p_bibl.add_argument("--bibl-xml", type=Path, default=DEFAULT_BIBL_XML,
                        help="source bibliographic XML (default: bibl.xml)")
    p_bibl.add_argument("--bibl-db", type=Path, default=DEFAULT_BIBL_DB,
                        help="output SQLite cache (default: var/bibl.sqlite)")

    p_index = sub.add_parser(
        "index", help="(optional) build the SQLite FTS5 full-text search engine")
    p_index.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p_index.add_argument("--db", type=Path, default=DEFAULT_DB,
                         help="output SQLite FTS5 database")
    p_index.add_argument("--jsonl", type=Path, default=None,
                         help="also write a JSONL dump of every passage")
    p_index.add_argument("-j", "--jobs", type=int, default=None,
                         help="parallel worker processes (default: all CPU cores)")
    p_index.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                         help="seconds to wait for any worker result before "
                              "giving up on wedged documents (default: "
                              f"{DEFAULT_TIMEOUT})")

    p_vec = sub.add_parser(
        "vectorize",
        help="(optional) build the semantic 'Similar passages' vector store")
    p_vec.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p_vec.add_argument("--vectors-db", type=Path, default=DEFAULT_VECTORS_DB,
                       help="output SQLite vector store")
    p_vec.add_argument("--model", default=DEFAULT_MODEL_ID,
                       help="Hugging Face model id (ONNX sentence-transformer)")
    p_vec.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR,
                       help="local directory the model is downloaded to / read from")
    p_vec.add_argument("-j", "--jobs", type=int, default=None,
                       help="parallel render workers (default: all CPU cores)")
    p_vec.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                       help="seconds to wait for any worker result before giving "
                            f"up on wedged documents (default: {DEFAULT_TIMEOUT})")
    p_vec.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                       help=f"encoder batch size (default: {DEFAULT_BATCH_SIZE}; "
                            "raise it for GPU, e.g. 128-256)")
    p_vec.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                       help="ONNX Runtime execution provider: auto uses CUDA when "
                            "onnxruntime-gpu + a GPU are present, else CPU "
                            "(default: auto)")
    p_vec.add_argument("--from-fts", type=Path, default=None,
                       help="reuse already-rendered leaf text from an FTS index "
                            "(built by `browse.py index`) instead of re-rendering "
                            "with Saxon; -j/--timeout are then unused")

    args = parser.parse_args(argv)

    if args.command == "bibl":
        n = build_bibl_cache(args.bibl_xml.resolve(), args.bibl_db.resolve())
        print(f"wrote {n} bibliographic entries → {args.bibl_db}")
        return 0

    if args.command == "index":
        jsonl = args.jsonl.resolve() if args.jsonl else None
        return build_index(args.root.resolve(), args.db.resolve(), jsonl,
                           args.jobs, args.timeout)

    if args.command == "vectorize":
        model_dir = ensure_model(args.model, args.model_dir.resolve())
        from_fts = args.from_fts.resolve() if args.from_fts else None
        return build_vectors(args.root.resolve(), args.vectors_db.resolve(),
                             model_dir, args.jobs, args.timeout, args.batch_size,
                             args.device, from_fts)

    # default: serve
    root = getattr(args, "root", DEFAULT_ROOT).resolve()
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 5000)
    db_path = getattr(args, "db", DEFAULT_DB).resolve()
    dts_base = getattr(args, "dts_base", DEFAULT_DTS_BASE)
    enable_fts = getattr(args, "fulltext", False)
    enable_vectors = getattr(args, "vectors", False)
    vectors_db = getattr(args, "vectors_db", DEFAULT_VECTORS_DB)
    bibl_db = getattr(args, "bibl_db", DEFAULT_BIBL_DB)
    debug = getattr(args, "debug", False)
    app = create_app(root, db_path=db_path, dts_base=dts_base, enable_fts=enable_fts,
                     enable_vectors=enable_vectors, vectors_db=vectors_db,
                     bibl_db=bibl_db)
    print(f"serving {root} on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
    return 0
