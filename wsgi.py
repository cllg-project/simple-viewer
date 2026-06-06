"""
WSGI entrypoint for production servers (gunicorn, uWSGI, ...).

    gunicorn wsgi:app -b 0.0.0.0:8000 -w 2

Configuration is taken from environment variables (with sensible defaults from
cllg_viewer):

    CLLG_ROOT       corpus data directory   (default ./corpus/data)
    CLLG_DB         FTS5 index path         (default ./var/search.sqlite)
    CLLG_DTS_BASE   DTS server base URL     (default http://localhost:8000)
    CLLG_FULLTEXT   "1"/"true" to enable the optional full-text search

Use sync workers (the default): each gunicorn worker is a separate process and
builds its own Saxon processor, which is the safe way to use saxonche.  Do NOT run
gunicorn with --preload.
"""
import os

from cllg_viewer import DEFAULT_DB, DEFAULT_DTS_BASE, DEFAULT_ROOT, create_app


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


app = create_app(
    root=DEFAULT_ROOT.resolve(),
    db_path=DEFAULT_DB.resolve(),
    dts_base=DEFAULT_DTS_BASE,
    enable_fts=_flag("CLLG_FULLTEXT"),
)
