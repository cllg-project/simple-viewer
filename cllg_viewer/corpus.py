"""
Corpus discovery, metadata reading, citation navigation and DTS identifier
lookup over a CapiTainS data layout (data/{author}/{work}/{author}.{work}.{edition}-{lang}1.xml).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, List, Optional
from urllib.parse import urlencode

from lxml import etree

from dapytains.tei.citeStructure import CitableUnit

from .rendering import ROOT_DIR

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #
# `make install` clones the corpus repo into ./corpus (CapiTainS layout: data/...).
# Override with the CLLG_ROOT / CLLG_DB env vars or the --root / --db flags.
DEFAULT_ROOT = Path(os.environ.get("CLLG_ROOT", str(ROOT_DIR / "corpus" / "data")))
DEFAULT_DB = Path(os.environ.get("CLLG_DB", str(ROOT_DIR / "var" / "search.sqlite")))
# A running dapytains/DTS server the reading page can deep-link into.
DEFAULT_DTS_BASE = os.environ.get("CLLG_DTS_BASE", "http://localhost:8000")

TEI_NS = "http://www.tei-c.org/ns/1.0"
# Files served as editions: CapiTainS naming ends with -<lang><n>.xml
EDITION_GLOBS = ("*-grc*.xml", "*-lat*.xml")
# Per-work catalog files that carry the DTS <resource identifier=... filepath=...>.
METADATA_NAMES = ("metadata.xml", "__cts__.xml")


# --------------------------------------------------------------------------- #
# Corpus discovery / metadata
# --------------------------------------------------------------------------- #
def iter_editions(root: Path) -> Iterator[Path]:
    """Yield every TEI edition file under `root` (CapiTainS data layout)."""
    seen: set[Path] = set()
    for pattern in EDITION_GLOBS:
        for path in root.rglob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def read_meta(path: Path) -> dict:
    """Cheap title/author read straight from the teiHeader (no full parse)."""
    title = author = ""
    try:
        for _, elem in etree.iterparse(str(path), events=("end",)):
            tag = etree.QName(elem).localname
            if tag == "title" and not title and elem.text:
                title = elem.text.strip()
            elif tag == "author" and not author and elem.text:
                author = elem.text.strip()
            elif tag == "teiHeader":
                break  # everything we need lives in the header
    except etree.XMLSyntaxError:
        pass
    return {"title": title, "author": author}


def safe_path(root: Path, file_arg: str) -> Path:
    """Resolve a ?file= argument and refuse anything outside `root`."""
    candidate = (root / file_arg).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        raise ValueError("path escapes corpus root")
    if not candidate.is_file():
        raise FileNotFoundError(file_arg)
    return candidate


def walk_reffs(units: List[CitableUnit]) -> Iterator[CitableUnit]:
    """Depth-first flatten of the nested citation tree."""
    for unit in units:
        yield unit
        if unit.children:
            yield from walk_reffs(unit.children)


def passage_neighbors(reffs_flat: List[CitableUnit],
                      ref: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(prev_ref, next_ref) in continuous document order at the current level.

    Navigation walks the flattened reference list, filtered to the units sharing
    the current ref's citation level, so reading flows leaf-to-leaf across parents:
    the last line of a fragment moves on to the first line of the next fragment.
    Returns (None, None) for the whole work or an unknown ref.
    """
    if not ref:
        return None, None
    current = next((u for u in reffs_flat if u.ref == ref), None)
    if current is None:
        return None, None
    same_level = [u.ref for u in reffs_flat if u.level == current.level]
    try:
        i = same_level.index(ref)
    except ValueError:
        return None, None
    prev_ref = same_level[i - 1] if i > 0 else None
    next_ref = same_level[i + 1] if i < len(same_level) - 1 else None
    return prev_ref, next_ref


# --------------------------------------------------------------------------- #
# DTS resource identifier (CTS URN) lookup
# --------------------------------------------------------------------------- #
_dts_cache: dict[Path, dict[Path, str]] = {}


def _load_catalog(meta_path: Path) -> dict[Path, str]:
    """Map {resolved TEI file -> resource identifier} from one metadata catalog."""
    out: dict[Path, str] = {}
    try:
        tree = etree.parse(str(meta_path))
    except etree.XMLSyntaxError:
        return out
    for res in tree.iter():
        if etree.QName(res).localname != "resource":
            continue
        ident = res.get("identifier")
        fp = res.get("filepath")
        if ident and fp:
            out[(meta_path.parent / fp).resolve()] = ident
    return out


def dts_identifier(path: Path) -> Optional[str]:
    """Find the DTS/CTS URN for a TEI file via the nearest catalog above it."""
    target = path.resolve()
    for ancestor in [target.parent, *target.parents]:
        for name in METADATA_NAMES:
            meta = ancestor / name
            if not meta.is_file():
                continue
            if meta not in _dts_cache:
                _dts_cache[meta] = _load_catalog(meta)
            if target in _dts_cache[meta]:
                return _dts_cache[meta][target]
    return None


def dts_document_url(base: str, identifier: str, ref: Optional[str],
                     tree: Optional[str]) -> str:
    """Build a DTS Document endpoint URL for a resource + reference."""
    params = {"resource": identifier}
    if ref:
        params["ref"] = ref
    if tree:
        params["tree"] = tree
    return f"{base.rstrip('/')}/document/?{urlencode(params)}"
