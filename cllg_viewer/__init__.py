"""
cllg-simple-viewer: browse a CapiTainS/DTS TEI corpus as HTML excerpts.

This package ties together:
  * dapytains  - resolves a citation reference to a *passage* (an excerpt).
  * saxonche   - runs the presentation stylesheets in xslt/ over that passage.
  * Flask      - serves an Author -> Work index and the rendered passages.

The presentation stylesheets are robust to receiving only part of a node, so
whatever dapytains hands us renders.  Public names are re-exported here so
`import cllg_viewer` is a single stable surface (tests, wsgi, scripts).
"""
from .betacode import to_greek
from .corpus import (DEFAULT_DB, DEFAULT_DTS_BASE, DEFAULT_ROOT, dts_document_url,
                     dts_identifier, iter_editions, passage_neighbors, read_meta,
                     safe_path, walk_reffs)
from .rendering import CSS_FILE, HTML_XSLT, ROOT_DIR, TEXT_XSLT, Renderer
from .search import (DEFAULT_TIMEOUT, _index_document, _worker_init, build_index,
                     fts_connect, fts_query_string, fts_search)
from .web import create_app

__all__ = [
    "Renderer", "create_app", "build_index", "to_greek",
    "walk_reffs", "passage_neighbors", "read_meta", "safe_path", "iter_editions",
    "dts_identifier", "dts_document_url",
    "fts_connect", "fts_query_string", "fts_search",
    "_index_document", "_worker_init",
    "DEFAULT_ROOT", "DEFAULT_DB", "DEFAULT_DTS_BASE", "DEFAULT_TIMEOUT",
    "ROOT_DIR", "HTML_XSLT", "TEXT_XSLT", "CSS_FILE",
]
