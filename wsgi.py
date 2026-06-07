"""
WSGI entrypoint for production servers (gunicorn, uWSGI, ...).

    gunicorn wsgi:app -b 0.0.0.0:8000 -w 2

Configuration is taken from environment variables (with sensible defaults from
cllg_viewer):

    CLLG_ROOT       corpus data directory   (default ./corpus/data)
    CLLG_DB         FTS5 index path         (default ./var/search.sqlite)
    CLLG_DTS_BASE   external DTS server base URL for the "DTS API" deep-link
                    (default: unset — the link is hidden, since the viewer is
                    not itself a DTS server)
    CLLG_FULLTEXT   "1"/"true" to enable the optional full-text search
    CLLG_VECTORS_ON "1"/"true" to enable the optional semantic "Similar passages"
    CLLG_VECTORS    vector store dir/db     (default ./var/vectors/vectors.sqlite)

Use sync workers (the default): each gunicorn worker is a separate process and
builds its own Saxon processor, which is the safe way to use saxonche.  Do NOT run
gunicorn with --preload.
"""
import os

from cllg_viewer import (DEFAULT_DB, DEFAULT_DTS_BASE, DEFAULT_ROOT,
                         DEFAULT_VECTORS_DB, create_app)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


app = create_app(
    root=DEFAULT_ROOT.resolve(),
    db_path=DEFAULT_DB.resolve(),
    dts_base=DEFAULT_DTS_BASE,
    enable_fts=_flag("CLLG_FULLTEXT"),
    enable_vectors=_flag("CLLG_VECTORS_ON"),
    vectors_db=DEFAULT_VECTORS_DB,
)
