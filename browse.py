#!/usr/bin/env python3
"""
browse.py - thin entry point for the CLLG simple viewer.

The implementation lives in the `cllg_viewer` package:

    browse.py serve  [--root data] [--host 127.0.0.1] [--port 5000]
                     [--fulltext --db var/search.sqlite] [--dts-base URL]
    browse.py bibl   [--bibl-xml bibl.xml] [--bibl-db var/bibl.sqlite]
    browse.py index  [--root data] [--db var/search.sqlite]
                     [--jsonl PATH] [-j JOBS] [--timeout SECONDS]

Run it from the repository root so the relative paths (xslt/, templates/,
static/, corpus/) resolve.
"""
from cllg_viewer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
