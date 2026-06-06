#!/usr/bin/env python3
"""
browse.py - a small local app to browse this TEI corpus as HTML excerpts.

It ties three things together:

  * dapytains  - resolves a citation reference to a *passage* (an excerpt: only
                 part of the document tree).  See Document.get_passage / get_reffs.
  * saxonche   - runs the presentation stylesheets in xslt/ over that passage.
  * Flask      - serves an index of works and the rendered passages in a browser.

The presentation stylesheets (xslt/tei-to-html.xsl and xslt/tei-to-text.xsl) are
robust to receiving only part of a node, so whatever dapytains hands us renders.

Usage
-----
    ./env/bin/python browse.py serve  [--root data] [--host 127.0.0.1] [--port 5000]
                                      [--fulltext --db scripts/out/search.sqlite]
                                      [--dts-base URL]
    ./env/bin/python browse.py index  [--root data] [--db scripts/out/search.sqlite]
                                      [--jsonl PATH]

`serve` launches the web UI (default).  The home page is organised Author -> Work
and its search box lets you **find an author or work to navigate to** -- that is
always on and needs no index.

Full-text search over passage *content* is an **optional** add-on: build the index
once with `index` (a self-contained SQLite FTS5 database -- no external service,
stdlib only -- from the note-free text stylesheet; `--jsonl` also dumps records),
then start the server with `--fulltext` to surface content matches alongside the
author/work results.

Each reading page also links to the matching **DTS API** document endpoint, built
from the resource's CTS URN (read from the work's `metadata.xml`) against
`--dts-base` (the URL of a running dapytains/DTS server).

Run it from the repository root so the relative paths resolve.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import multiprocessing as mp
import sqlite3
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from lxml import etree
from saxonche import PySaxonProcessor

from dapytains.tei.document import Document
from dapytains.tei.citeStructure import CitableUnit

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
XSLT_DIR = ROOT_DIR / "xslt"
HTML_XSLT = XSLT_DIR / "tei-to-html.xsl"
TEXT_XSLT = XSLT_DIR / "tei-to-text.xsl"
CSS_FILE = XSLT_DIR / "tei.css"
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
# XSLT engine (one long-lived Saxon processor for the whole process)
# --------------------------------------------------------------------------- #
class Renderer:
    """Compiles the two stylesheets once and transforms passages on demand."""

    def __init__(self) -> None:
        self._proc = PySaxonProcessor(license=False)
        xslt = self._proc.new_xslt30_processor()
        self._html = xslt.compile_stylesheet(stylesheet_file=str(HTML_XSLT))
        self._text = xslt.compile_stylesheet(stylesheet_file=str(TEXT_XSLT))

    def render(self, passage: etree._Element, mode: str = "html") -> str:
        """Transform an lxml passage element to HTML (mode='html') or text."""
        xml = etree.tostring(passage, encoding="unicode")
        node = self._proc.parse_xml(xml_text=xml)
        executable = self._html if mode == "html" else self._text
        out = executable.transform_to_string(xdm_node=node)
        return out if out is not None else ""


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
    from urllib.parse import urlencode
    params = {"resource": identifier}
    if ref:
        params["ref"] = ref
    if tree:
        params["tree"] = tree
    return f"{base.rstrip('/')}/document/?{urlencode(params)}"


# --------------------------------------------------------------------------- #
# Self-contained full-text search engine (SQLite FTS5)
# --------------------------------------------------------------------------- #
def fts_connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open the FTS index read-only; return None if it has not been built."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query_string(raw: str) -> str:
    """Turn free user input into a safe FTS5 MATCH expression (AND of terms)."""
    terms = re.findall(r"\w+", raw, flags=re.UNICODE)
    return " AND ".join(f'"{t}"' for t in terms)


def fts_search(conn: sqlite3.Connection, raw: str, limit: int = 50) -> List[dict]:
    match = fts_query_string(raw)
    if not match:
        return []
    rows = conn.execute(
        """SELECT file, tree, ref, title, author,
                  snippet(passages, 5, '<mark>', '</mark>', ' … ', 12) AS snippet
           FROM passages
           WHERE passages MATCH ?
           ORDER BY rank
           LIMIT ?""",
        (match, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Web application
# --------------------------------------------------------------------------- #
def create_app(root: Path, db_path: Path = DEFAULT_DB,
               dts_base: str = DEFAULT_DTS_BASE, enable_fts: bool = False):
    from collections import OrderedDict

    from flask import Flask, abort, render_template_string, request, send_file
    from markupsafe import Markup

    app = Flask(__name__)
    renderer = Renderer()

    # Building the catalog scans every file's teiHeader, so cache it in process
    # (the corpus is static while the server runs).  Each gunicorn worker keeps
    # its own copy; restart the server to pick up corpus changes.
    _catalog: List[dict] = []

    def all_works() -> List[dict]:
        if _catalog:
            return _catalog
        for path in sorted(iter_editions(root)):
            rel = str(path.relative_to(root))
            meta = read_meta(path)
            # author id = first path segment (e.g. tlg1326); work id = second.
            parts = Path(rel).parts
            _catalog.append({
                "file": rel,
                "author_id": parts[0] if parts else rel,
                "work_id": parts[1] if len(parts) > 1 else "",
                **meta,
            })
        return _catalog

    def group_by_author(works: List[dict]) -> "OrderedDict":
        """Author -> [works], so the home page navigates Author > Work."""
        groups: "OrderedDict[str, dict]" = OrderedDict()
        for w in works:
            key = w["author"] or w["author_id"]
            g = groups.setdefault(key, {"author": key, "id": w["author_id"], "works": []})
            g["works"].append(w)
        # sort authors alphabetically, works by title within an author
        ordered = OrderedDict(sorted(groups.items(), key=lambda kv: kv[0].lower()))
        for g in ordered.values():
            g["works"].sort(key=lambda w: (w["title"] or w["file"]).lower())
        return ordered

    # Optional full-text search is only on when explicitly enabled *and* built.
    fts_on = enable_fts and db_path.is_file()

    @app.route("/")
    def home():
        q = (request.args.get("q") or "").strip()
        works = all_works()
        hits = []
        if q:
            # Always-on: navigate by searching author / work names.
            ql = q.lower()
            works = [w for w in works
                     if ql in (w["title"] or "").lower()
                     or ql in (w["author"] or "").lower()
                     or ql in w["file"].lower()]
            # Optional add-on: full-text content search (opt-in via --fulltext).
            if fts_on:
                conn = fts_connect(db_path)
                if conn is not None:
                    hits = fts_search(conn, q)
                    conn.close()
        groups = group_by_author(works)
        return render_template_string(
            INDEX_TPL, groups=groups, count=sum(len(g["works"]) for g in groups.values()),
            root=root, q=q, hits=hits, fts_on=fts_on)

    @app.route("/doc")
    def doc():
        rel = request.args.get("file", "")
        try:
            path = safe_path(root, rel)
        except (ValueError, FileNotFoundError):
            abort(404)
        document = Document(str(path))
        trees = list(document.citeStructure.keys())
        tree = request.args.get("tree") or document.default_tree
        reffs = list(walk_reffs(document.get_reffs(tree)))
        meta = read_meta(path)
        return render_template_string(
            DOC_TPL, file=rel, meta=meta, reffs=reffs,
            trees=trees, tree=tree,
        )

    @app.route("/passage")
    def passage():
        rel = request.args.get("file", "")
        ref = request.args.get("ref") or None
        tree = request.args.get("tree") or None
        mode = "text" if request.args.get("format") == "text" else "html"
        try:
            path = safe_path(root, rel)
        except (ValueError, FileNotFoundError):
            abort(404)
        document = Document(str(path))
        tree_name = tree or document.default_tree
        try:
            node = document.get_passage(ref_or_start=ref, tree=tree)
        except Exception as exc:  # invalid ref, etc.
            abort(404, description=str(exc))
        if mode == "text":
            from flask import Response
            return Response(renderer.render(node, "text"), mimetype="text/plain")
        body = Markup(renderer.render(node, "html"))
        urn = dts_identifier(path)
        dts_url = dts_document_url(dts_base, urn, ref, tree) if urn else None
        # previous / next sibling passage for in-place reading navigation
        prev_ref = next_ref = None
        if ref:
            flat = list(walk_reffs(document.get_reffs(tree_name)))
            prev_ref, next_ref = passage_neighbors(flat, ref)
        pager = Markup(render_template_string(
            PAGER_TPL, file=rel, tree=tree_name, prev_ref=prev_ref, next_ref=next_ref))
        return render_template_string(
            PASSAGE_TPL, file=rel, ref=ref or "(whole work)", tree=tree_name, body=body,
            urn=urn, dts_url=dts_url, prev_ref=prev_ref, next_ref=next_ref, pager=pager,
        )

    @app.route("/tei.css")
    def css():
        return send_file(CSS_FILE, mimetype="text/css")

    return app


# --------------------------------------------------------------------------- #
# Templates (kept inline so the app is a single file)
# --------------------------------------------------------------------------- #
_BASE_CSS = """
  body { font-family: system-ui, sans-serif; margin: 0; color: #222; }
  header { background: #2c3e50; color: #fff; padding: .8rem 1.2rem; }
  header a { color: #fff; text-decoration: none; }
  main { padding: 1.2rem; }
  .layout { display: flex; gap: 1.5rem; align-items: flex-start; }
  nav.reffs { flex: 0 0 16rem; max-height: 80vh; overflow: auto;
              border-right: 1px solid #eee; padding-right: 1rem; font-size: .9rem; }
  nav.reffs a { display: block; padding: .1rem .3rem; text-decoration: none; color: #06c; }
  nav.reffs a:hover { background: #f0f6ff; }
  ul.works { list-style: none; padding: 0; }
  ul.works li { padding: .35rem 0; border-bottom: 1px solid #f0f0f0; }
  .muted { color: #888; font-size: .85rem; }
  form.search { margin: 0 0 1rem; }
  form.search input[type=search] { padding: .4rem .6rem; width: 22rem; font-size: 1rem; }
  form.search button { padding: .4rem .8rem; font-size: 1rem; }
  details.author { margin: .2rem 0; }
  details.author > summary { cursor: pointer; padding: .35rem 0; font-weight: 600; }
  details.author ul.works { margin: 0 0 .4rem 1.2rem; }
  .hits { margin: 0 0 1.5rem; }
  .hit { padding: .4rem 0; border-bottom: 1px solid #f0f0f0; }
  .hit .snip { color: #333; }
  .hit mark { background: #ffe9a8; }
  .dts { font-size: .85rem; }
  .dts code { background: #f3f3f3; padding: .1rem .3rem; border-radius: 3px; }
  nav.pager { display: flex; justify-content: space-between; gap: 1rem; margin: .8rem 0; }
  nav.pager a, nav.pager span { padding: .3rem .7rem; border: 1px solid #ddd;
                                border-radius: 4px; text-decoration: none; color: #06c; }
  nav.pager a:hover { background: #f0f6ff; }
  nav.pager .next { margin-left: auto; }
"""

INDEX_TPL = """<!doctype html><meta charset="utf-8"><title>Corpus browser</title>
<style>%s</style>
<header><a href="/"><strong>Corpus browser</strong></a> &middot; {{ count }} works under <code>{{ root }}</code></header>
<main>
  <form class="search" action="/" method="get">
    <input type="search" name="q" value="{{ q }}"
           placeholder="search author or work{%% if fts_on %%} (or text){%% endif %%}…" autofocus>
    <button type="submit">Search</button>
  </form>

  {%% if q and fts_on and hits %%}
  <div class="hits">
    <p class="muted">{{ hits|length }} passage matches</p>
    {%% for h in hits %%}
      <div class="hit">
        <a href="/passage?file={{ h.file|urlencode }}&ref={{ h.ref|urlencode }}&tree={{ h.tree }}">
           {{ h.title or h.file }} <span class="muted">{{ h.author }} &middot; {{ h.ref }}</span></a>
        <div class="snip">{{ h.snippet|safe }}</div>
      </div>
    {%% endfor %%}
  </div>
  {%% elif q and fts_on %%}
  <p class="muted">no passage matches for “{{ q }}”.</p>
  {%% endif %%}

  {%% for author, g in groups.items() %%}
    <details class="author"{%% if q %%} open{%% endif %%}>
      <summary>{{ author }} <span class="muted">({{ g.works|length }})</span></summary>
      <ul class="works">
      {%% for w in g.works %%}
        <li><a href="/doc?file={{ w.file|urlencode }}">{{ w.title or w.work_id }}</a>
            <span class="muted">{{ w.work_id }}</span></li>
      {%% endfor %%}
      </ul>
    </details>
  {%% else %%}
    <p class="muted">nothing matches “{{ q }}”.</p>
  {%% endfor %%}
</main>""" % _BASE_CSS

DOC_TPL = """<!doctype html><meta charset="utf-8"><title>{{ meta.title }}</title>
<style>%s</style>
<header><a href="/">&larr; works</a> &middot; <strong>{{ meta.title }}</strong>
  <span class="muted">{{ meta.author }}</span></header>
<main>
  {%% if trees|length > 1 %%}<p class="muted">trees:
    {%% for t in trees %%}<a href="/doc?file={{ file|urlencode }}&tree={{ t }}">{{ t }}</a> {%% endfor %%}</p>{%% endif %%}
  <p class="muted">{{ reffs|length }} citeable passages &middot;
     <a href="/passage?file={{ file|urlencode }}&tree={{ tree }}">read whole work</a></p>
  <nav class="reffs">
    {%% for r in reffs %%}
      <a style="padding-left: {{ (r.level-1) * 0.8 }}rem"
         href="/passage?file={{ file|urlencode }}&ref={{ r.ref|urlencode }}&tree={{ tree }}"
         >{{ r.ref }} <span class="muted">{{ r.citeType }}</span></a>
    {%% endfor %%}
  </nav>
</main>""" % _BASE_CSS

PASSAGE_TPL = """<!doctype html><meta charset="utf-8"><title>{{ ref }}</title>
<link rel="stylesheet" href="/tei.css">
<style>%s</style>
<header><a href="/doc?file={{ file|urlencode }}{%% if tree %%}&tree={{ tree }}{%% endif %%}">&larr; passages</a>
  &middot; <strong>{{ ref }}</strong>
  &middot; <a href="/passage?file={{ file|urlencode }}&ref={{ ref }}&tree={{ tree }}&format=text">plain text</a>
  {%% if dts_url %%}&middot; <a href="{{ dts_url }}">DTS API</a>{%% endif %%}
</header>
<main>
  {%% if urn %%}<p class="dts muted">DTS resource <code>{{ urn }}</code>
    &middot; <a href="{{ dts_url }}">document endpoint</a></p>{%% endif %%}
  {%% if prev_ref or next_ref %%}{{ pager }}{%% endif %%}
  <div class="tei-text">{{ body }}</div>
  {%% if prev_ref or next_ref %%}{{ pager }}{%% endif %%}
</main>""" % _BASE_CSS

# Reusable prev/next pager (rendered top and bottom of a passage).
PAGER_TPL = """<nav class="pager">
  {% if prev_ref %}<a class="prev" rel="prev"
     href="/passage?file={{ file|urlencode }}&ref={{ prev_ref|urlencode }}&tree={{ tree }}"
     >&larr; {{ prev_ref }}</a>{% else %}<span class="prev muted">&larr;</span>{% endif %}
  {% if next_ref %}<a class="next" rel="next"
     href="/passage?file={{ file|urlencode }}&ref={{ next_ref|urlencode }}&tree={{ tree }}"
     >{{ next_ref }} &rarr;</a>{% else %}<span class="next muted">&rarr;</span>{% endif %}
</nav>"""


# --------------------------------------------------------------------------- #
# `index` subcommand: build the search engine (parallel, one worker per document)
# --------------------------------------------------------------------------- #
# Rendering each passage (dapytains + Saxon XSLT) is CPU-bound and independent per
# document, so we fan the documents out across a process pool.  Each worker builds
# its own Saxon processor once (in the pool initializer) — that is the safe way to
# use saxonche across forked processes; we never share a processor across processes.
_WORKER_RENDERER: Optional["Renderer"] = None

# A row destined for the index: (file, tree, ref, title, author, text).
_Row = Tuple[str, str, str, str, str, str]


def _worker_init() -> None:
    global _WORKER_RENDERER
    _WORKER_RENDERER = Renderer()


def _index_document(task: Tuple[str, str]) -> Tuple[str, Optional[str], List[_Row]]:
    """Render every passage of one document.

    Returns (rel, error, rows).  `error` is a short reason string when the
    document could not be (fully) processed, else None.  A worker must NEVER
    raise: some corpus files have a citeStructure dapytains cannot handle (e.g.
    unit names that compile to an invalid regex group), and one bad file must not
    abort the whole pool — it is skipped and reported instead.
    """
    rel, path_str = task
    try:
        document = Document(path_str)
    except Exception as exc:  # noqa: BLE001 - unparseable / unsupported file
        return rel, f"open failed: {type(exc).__name__}: {exc}", []
    meta = read_meta(Path(path_str))
    renderer = _WORKER_RENDERER
    rows: List[_Row] = []
    errors: List[str] = []
    for tree in document.citeStructure:
        try:
            units = list(walk_reffs(document.get_reffs(tree)))
        except Exception as exc:  # noqa: BLE001 - broken citeStructure for this tree
            errors.append(f"tree '{tree}': {type(exc).__name__}: {exc}")
            continue
        for unit in units:
            try:
                node = document.get_passage(ref_or_start=unit.ref, tree=tree)
                text = renderer.render(node, "text").strip()
            except Exception:  # noqa: BLE001 - skip the individual passage
                continue
            if text:
                rows.append((rel, tree, unit.ref, meta["title"], meta["author"], text))
    return rel, ("; ".join(errors) if errors else None), rows


def build_index(root: Path, db_path: Path, jsonl: Optional[Path] = None,
                jobs: Optional[int] = None) -> int:
    jobs = jobs or os.cpu_count() or 1
    tasks = [(str(p.relative_to(root)), str(p)) for p in sorted(iter_editions(root))]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    # unicode61 case-folds and (remove_diacritics 2) folds Latin diacritics, so the
    # Latin corpus searches accent-insensitively.  Note: SQLite's tokenizer does NOT
    # fold Greek accents, so Greek search is accent-sensitive (type the accents).
    conn.execute(
        """CREATE VIRTUAL TABLE passages USING fts5(
               file UNINDEXED, tree UNINDEXED, ref UNINDEXED,
               title, author, text,
               tokenize = 'unicode61 remove_diacritics 2')""")

    jh = jsonl.open("w", encoding="utf-8") if jsonl else None
    if jsonl:
        jsonl.parent.mkdir(parents=True, exist_ok=True)
    n_docs = n_passages = n_done = 0
    total = len(tasks)
    skipped: List[Tuple[str, str]] = []  # (file, reason) for the skip report

    def consume(result: Tuple[str, Optional[str], List[_Row]]) -> None:
        nonlocal n_docs, n_passages, n_done
        rel, error, rows = result
        n_done += 1
        if error:
            skipped.append((rel, error))
            print(f"skip {rel}: {error}", file=sys.stderr)
        if rows:
            n_docs += 1
            conn.executemany(
                "INSERT INTO passages(file, tree, ref, title, author, text)"
                " VALUES (?,?,?,?,?,?)", rows)
            if jh:
                for rel_, tree, ref, title, author, text in rows:
                    jh.write(json.dumps(
                        {"file": rel_, "tree": tree, "ref": ref,
                         "title": title, "author": author, "text": text},
                        ensure_ascii=False) + "\n")
            n_passages += len(rows)
            conn.commit()
        if n_done % 200 == 0 or n_done == total:
            print(f"  {n_done}/{total} documents "
                  f"({n_passages} passages, {len(skipped)} skipped)…", file=sys.stderr)

    try:
        if jobs == 1 or total <= 1:
            _worker_init()
            for task in tasks:
                consume(_index_document(task))
        else:
            # Use the "spawn" start method: each worker is a *fresh* process that
            # builds its own Saxon processor in the initializer.  SaxonC starts
            # native threads, and forking a multi-threaded parent risks deadlocks,
            # so we deliberately avoid fork here.  The `if __name__ == "__main__"`
            # guard keeps the re-import that spawn does from re-running the CLI.
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=jobs, initializer=_worker_init) as pool:
                for result in pool.imap_unordered(_index_document, tasks, chunksize=1):
                    consume(result)
    finally:
        if jh:
            jh.close()
    conn.commit()
    conn.close()
    print(f"indexed {n_passages} passages from {n_docs} documents "
          f"using {jobs} job(s) -> {db_path}" + (f" (+ {jsonl})" if jsonl else ""))
    if skipped:
        report = db_path.with_suffix(".skipped.tsv")
        with report.open("w", encoding="utf-8") as fh:
            fh.write("file\treason\n")
            for rel, reason in skipped:
                fh.write(f"{rel}\t{reason}\n")
        print(f"skipped {len(skipped)} document(s) with citation issues "
              f"-> {report}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
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

    args = parser.parse_args(argv)

    if args.command == "index":
        jsonl = args.jsonl.resolve() if args.jsonl else None
        return build_index(args.root.resolve(), args.db.resolve(), jsonl, args.jobs)

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


if __name__ == "__main__":
    raise SystemExit(main())
