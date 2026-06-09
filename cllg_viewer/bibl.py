"""
Bibliographic cache: parse bibl.xml (TLG-style TEI <biblStruct> database) into
a small SQLite table keyed by "AAAA WWW" (4-digit author + space + 3-digit work),
then look up edition metadata for a CTS URN at read time.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from lxml import etree

from .rendering import ROOT_DIR

DEFAULT_BIBL_XML = Path(os.environ.get("CLLG_BIBL_XML", str(ROOT_DIR / "bibl.xml")))
DEFAULT_BIBL_DB = Path(os.environ.get("CLLG_BIBL_DB", str(ROOT_DIR / "var" / "bibl.sqlite")))

_CREATE = """
CREATE TABLE IF NOT EXISTS bibl (
    key       TEXT PRIMARY KEY,
    author    TEXT,
    editor    TEXT,
    title     TEXT,
    publisher TEXT,
    pub_place TEXT,
    date      TEXT,
    pages     TEXT
)
"""


def urn_to_bibl_key(urn: str) -> Optional[str]:
    """Map 'urn:cts:greekLit:tlg0001.tlg001.cllg-grc1' → '0001 001'."""
    try:
        parts = urn.split(":")[-1].split(".")
        author = parts[0].lstrip("tlgTLG").zfill(4)
        work = parts[1].lstrip("tlgTLG").zfill(3)
        return f"{author} {work}"
    except (IndexError, AttributeError):
        return None


def build_bibl_cache(bibl_xml: Path = DEFAULT_BIBL_XML,
                     db_path: Path = DEFAULT_BIBL_DB) -> int:
    """Parse bibl.xml and write a SQLite lookup table. Returns number of rows written."""
    tree = etree.parse(str(bibl_xml))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE)

    rows = []
    for struct in tree.getroot().iter("biblStruct"):
        key = struct.get("n")
        if not key:
            continue
        author = editor = title = publisher = pub_place = date = pages = ""
        for child in struct.iter():
            tag = etree.QName(child).localname
            text = (child.text or "").strip()
            if not text:
                continue
            parent_tag = etree.QName(child.getparent()).localname if child.getparent() is not None else ""
            if tag == "persName" and parent_tag == "author" and not author:
                author = text
            elif tag == "persName" and parent_tag == "editor" and not editor:
                editor = text
            elif tag == "title" and not title:
                title = text
            elif tag == "publisher" and not publisher:
                publisher = text
            elif tag == "pubPlace" and not pub_place:
                pub_place = text
            elif tag == "date" and child.get("type") is None and not date:
                date = text
            elif tag == "biblScope" and child.get("unit") == "page" and not pages:
                pages = text
        rows.append((key, author, editor, title, publisher, pub_place, date, pages))

    conn.executemany(
        "INSERT OR REPLACE INTO bibl VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def bibl_connect(db_path: Path = DEFAULT_BIBL_DB) -> Optional[sqlite3.Connection]:
    """Open bibl.sqlite read-only; return None if not built yet."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_bibl(conn: Optional[sqlite3.Connection], urn: Optional[str]) -> Optional[dict]:
    """Return a bibl record dict for the given URN, or None."""
    if not conn or not urn:
        return None
    key = urn_to_bibl_key(urn)
    if not key:
        return None
    row = conn.execute("SELECT * FROM bibl WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None
