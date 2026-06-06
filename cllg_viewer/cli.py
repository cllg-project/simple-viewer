"""
Command-line interface: `serve` (the web UI, default) and `index` (build the
optional SQLite FTS5 full-text search engine).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from .corpus import DEFAULT_DB, DEFAULT_DTS_BASE, DEFAULT_ROOT
from .search import DEFAULT_TIMEOUT, build_index
from .web import create_app

__doc__ = """\
browse the CLLG TEI corpus as HTML excerpts.

    browse.py serve  [--root data] [--host 127.0.0.1] [--port 5000]
                     [--fulltext --db var/search.sqlite] [--dts-base URL]
    browse.py index  [--root data] [--db var/search.sqlite]
                     [--jsonl PATH] [-j JOBS] [--timeout SECONDS]

`serve` launches the web UI (default).  Its home page is organised Author -> Work
and the search box finds an author or work to navigate to (always on, no index).

Full-text search over passage *content* is an optional add-on: build the index
once with `index`, then start the server with `--fulltext`.
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
    p_serve.add_argument("--debug", action="store_true")

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

    args = parser.parse_args(argv)

    if args.command == "index":
        jsonl = args.jsonl.resolve() if args.jsonl else None
        return build_index(args.root.resolve(), args.db.resolve(), jsonl,
                           args.jobs, args.timeout)

    # default: serve
    root = getattr(args, "root", DEFAULT_ROOT).resolve()
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 5000)
    db_path = getattr(args, "db", DEFAULT_DB).resolve()
    dts_base = getattr(args, "dts_base", DEFAULT_DTS_BASE)
    enable_fts = getattr(args, "fulltext", False)
    debug = getattr(args, "debug", False)
    app = create_app(root, db_path=db_path, dts_base=dts_base, enable_fts=enable_fts)
    print(f"serving {root} on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
    return 0
