"""
Flask web UI: an Author -> Work home page, a per-work citation tree, and the
rendered passages.  Templates live in ../templates and styles in ../static.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import List
from urllib.parse import urlencode

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_file)
from markupsafe import Markup

from .betacode import to_greek
from .corpus import (DEFAULT_DB, DEFAULT_DTS_BASE, dts_document_url,
                     dts_identifier, iter_editions, passage_neighbors,
                     read_meta, safe_path, walk_reffs)
from .rendering import CSS_FILE, ROOT_DIR, Renderer
from .search import (FTS_PAGE_SIZE, fts_connect, fts_count, fts_excerpts,
                     fts_search, fts_similarity_context)
from .vectors import DEFAULT_VECTORS_DB, VectorStore

from dapytains.tei.document import Document

TEMPLATE_DIR = str(ROOT_DIR / "templates")
STATIC_DIR = str(ROOT_DIR / "static")

ALPHABET = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _letter_of(name: str) -> str:
    """First A-Z letter of an author name (stripping a leading '['); else '#'."""
    for ch in (name or "").lstrip("[ ").upper():
        if ch in ALPHABET:
            return ch
        break
    return "#"


def _reffs_view(reffs_flat, current=None) -> List[dict]:
    """Flatten CitableUnits into template rows with a 0-based indent level."""
    base = min((u.level for u in reffs_flat), default=0)
    return [{"ref": u.ref, "level": max(0, u.level - base),
             "citeType": u.citeType or "", "current": (u.ref == current)}
            for u in reffs_flat]


def _level_names(reffs_flat) -> List[str]:
    """Ordered, de-duplicated citation level names (citeType) for the work."""
    seen: List[str] = []
    for u in reffs_flat:
        if u.citeType and u.citeType not in seen:
            seen.append(u.citeType)
    return seen


def _page_window(page: int, pages: int) -> List:
    """Compact 1-based page list with None for gaps: 1 … (page-1 page page+1) … N."""
    if pages <= 7:
        return list(range(1, pages + 1))
    out: List = [1]
    lo, hi = max(2, page - 1), min(pages - 1, page + 1)
    if lo > 2:
        out.append(None)
    out.extend(range(lo, hi + 1))
    if hi < pages - 1:
        out.append(None)
    out.append(pages)
    return out


def create_app(root: Path, db_path: Path = DEFAULT_DB,
               dts_base: str = DEFAULT_DTS_BASE, enable_fts: bool = False,
               enable_vectors: bool = False,
               vectors_db: Path = DEFAULT_VECTORS_DB):
    app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
    renderer = Renderer()

    # Building the catalog scans every file's teiHeader, so cache it in process
    # (the corpus is static while the server runs).  Each gunicorn worker keeps
    # its own copy; restart the server to pick up corpus changes.  The same scan
    # builds the URN <-> path maps: the URN is the app's primary public identifier
    # (URLs carry ?urn=…), but dapytains/Saxon and the search indexes address files
    # by path, so we translate at the edges via this cached map.
    _catalog: List[dict] = []
    _urn_to_file: dict = {}
    _file_to_urn: dict = {}

    def all_works() -> List[dict]:
        if _catalog:
            return _catalog
        for path in sorted(iter_editions(root)):
            rel = str(path.relative_to(root))
            meta = read_meta(path)
            urn = dts_identifier(path)
            # author id = first path segment (e.g. tlg1326); work id = second.
            parts = Path(rel).parts
            _catalog.append({
                "file": rel, "urn": urn,
                "author_id": parts[0] if parts else rel,
                "work_id": parts[1] if len(parts) > 1 else "",
                **meta,
            })
            if urn:
                _urn_to_file[urn] = rel
                _file_to_urn[rel] = urn
        return _catalog

    def urn_for(rel: str):
        all_works()
        return _file_to_urn.get(rel)

    def file_for(urn: str):
        all_works()
        return _urn_to_file.get(urn)

    def resolve_rel(args) -> str:
        """The file path a request addresses, from ?urn= (preferred) or ?file=.

        URN is the primary identifier; ?file= stays a fallback so works lacking a
        catalogued URN (and older links) keep working.
        """
        urn = args.get("urn")
        if urn:
            return file_for(urn) or ""
        return args.get("file", "")

    def passage_url(file=None, ref=None, tree=None, fmt=None, urn=None) -> str:
        """Build a /passage URL keyed by URN when one exists, else by path."""
        urn = urn or (urn_for(file) if file else None)
        params = {"urn": urn} if urn else ({"file": file} if file else {})
        if ref:
            params["ref"] = ref
        if tree:
            params["tree"] = tree
        if fmt:
            params["format"] = fmt
        return "/passage?" + urlencode(params)

    def doc_url(file=None, tree=None, urn=None) -> str:
        urn = urn or (urn_for(file) if file else None)
        params = {"urn": urn} if urn else ({"file": file} if file else {})
        if tree:
            params["tree"] = tree
        return "/doc?" + urlencode(params)

    app.jinja_env.globals.update(passage_url=passage_url, doc_url=doc_url)

    def group_by_author(works: List[dict]) -> "OrderedDict":
        """Author -> [works], so the home page navigates Author > Work."""
        groups: "OrderedDict[str, dict]" = OrderedDict()
        for w in works:
            key = w["author"] or w["author_id"]
            g = groups.setdefault(key, {"author": key, "id": w["author_id"], "works": []})
            g["works"].append(w)
        # sort authors alphabetically, works by title within an author
        ordered = OrderedDict(sorted(groups.items(), key=lambda kv: kv[0].lower()))
        for name, g in ordered.items():
            g["works"].sort(key=lambda w: (w["title"] or w["file"]).lower())
            g["letter"] = _letter_of(name)
        return ordered

    # Optional full-text search is only on when explicitly enabled *and* built.
    fts_on = enable_fts and db_path.is_file()

    # Optional semantic "Similar passages" store.  Opened once per worker process
    # (read-only, memmap-cheap); None when disabled, not built, or the host's
    # sqlite3 cannot load the vector extension — in which case the panel is simply
    # never shown (graceful off, like fts_on above).
    vectors = VectorStore.open(vectors_db) if enable_vectors else None
    vectors_on = vectors is not None

    @app.route("/")
    def home():
        q_raw = (request.args.get("q") or "").strip()
        # Two distinct search fields: "meta" filters the author/work catalogue,
        # "text" runs the full-text content engine.  Text needs a built index, so
        # fall back to meta when full-text is unavailable.
        scope = request.args.get("scope") or "meta"
        if scope == "text" and not fts_on:
            scope = "meta"
        # Optional Beta Code input method (Greek typed as ASCII -> Unicode).  The
        # browser converts the field live; we also convert here for no-JS / direct
        # URLs.  to_greek is a no-op on text that is already Greek/Latin.
        beta = (request.args.get("beta") or "").lower() in ("1", "on", "true", "yes")
        q = to_greek(q_raw) if (beta and q_raw) else q_raw

        # 1-based result page for full-text search.
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1

        works = all_works()
        groups = hits = None
        total_hits = 0
        pages = 1
        if q and scope == "text":
            conn = fts_connect(db_path)
            if conn is not None:
                total_hits = fts_count(conn, q)
                pages = max(1, -(-total_hits // FTS_PAGE_SIZE))  # ceil
                page = min(page, pages)
                hits = fts_search(conn, q, FTS_PAGE_SIZE, (page - 1) * FTS_PAGE_SIZE)
                conn.close()
            else:
                hits = []
        else:
            # metadata navigation: filter the catalogue by author / work name.
            if q:
                ql = q.lower()
                works = [w for w in works
                         if ql in (w["title"] or "").lower()
                         or ql in (w["author"] or "").lower()
                         or ql in w["file"].lower()]
            groups = group_by_author(works)
        present = sorted({g["letter"] for g in groups.values()}) if groups else []
        first_hit = (page - 1) * FTS_PAGE_SIZE + 1 if total_hits else 0
        last_hit = min(total_hits, page * FTS_PAGE_SIZE)
        return render_template(
            "index.html", groups=groups, hits=hits,
            count=(sum(len(g["works"]) for g in groups.values()) if groups else 0),
            authors_shown=(len(groups) if groups else 0),
            present=present, alpha=ALPHABET,
            total=len(all_works()), root=root, q=q_raw, q_greek=q,
            scope=scope, beta=beta, fts_on=fts_on,
            page=page, pages=pages, total_hits=total_hits,
            page_size=FTS_PAGE_SIZE, page_win=_page_window(page, pages),
            first_hit=first_hit, last_hit=last_hit)

    @app.route("/doc")
    def doc():
        rel = resolve_rel(request.args)
        try:
            path = safe_path(root, rel)
        except (ValueError, FileNotFoundError):
            abort(404)
        document = Document(str(path))
        trees = list(document.citeStructure.keys())
        tree = request.args.get("tree") or document.default_tree
        flat = list(walk_reffs(document.get_reffs(tree)))
        meta = read_meta(path)
        urn = dts_identifier(path)
        dts_url = dts_document_url(dts_base, urn, None, tree) if (urn and dts_base) else None
        # The landing renders the work's opening passage inline; failure to slice
        # it must not break the page, so fall back to no preview.
        first_ref = flat[0].ref if flat else None
        next_ref = flat[1].ref if len(flat) > 1 else None
        opening_html = None
        if first_ref:
            try:
                node = document.get_passage(ref_or_start=first_ref, tree=tree)
                opening_html = Markup(renderer.render(node, "html"))
            except Exception:  # noqa: BLE001 - preview is best-effort
                opening_html = None
        base_level = min((u.level for u in flat), default=0)
        toplevel = sum(1 for u in flat if u.level == base_level)
        return render_template(
            "doc.html", file=rel, meta=meta, reffs=_reffs_view(flat),
            levels=_level_names(flat), units=len(flat), toplevel=toplevel,
            trees=trees, tree=tree, urn=urn, dts_url=dts_url, current=None,
            first_ref=first_ref, next_ref=next_ref, opening_html=opening_html)

    @app.route("/passage")
    def passage():
        rel = resolve_rel(request.args)
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
            return Response(renderer.render(node, "text"), mimetype="text/plain")
        body = Markup(renderer.render(node, "html"))
        urn = dts_identifier(path)
        dts_url = dts_document_url(dts_base, urn, ref, tree) if (urn and dts_base) else None
        meta = read_meta(path)
        # The whole citation tree feeds the reader's nav rail; prev/next drive the
        # in-place reading pager.
        flat = list(walk_reffs(document.get_reffs(tree_name)))
        prev_ref, next_ref = passage_neighbors(flat, ref) if ref else (None, None)
        # Semantic neighbours are fetched on demand (the "Find similar passages"
        # button -> /similar), so the page load stays free of the KNN scan.  We only
        # tell the template whether to offer the button.
        return render_template(
            "passage.html", file=rel, ref=ref or "(whole work)", current=ref,
            tree=tree_name, body=body, urn=urn, dts_url=dts_url, meta=meta,
            reffs=_reffs_view(flat, current=ref), levels=_level_names(flat),
            units=len(flat), prev_ref=prev_ref, next_ref=next_ref,
            vectors_on=vectors_on)

    @app.route("/similar")
    def similar_api():
        """JSON neighbours for one passage, used by the reading page's on-demand
        'Find similar passages' button."""
        if not vectors_on:
            return jsonify({"hits": []})
        rel = resolve_rel(request.args)
        ref = request.args.get("ref") or None
        tree = request.args.get("tree") or None
        if not ref:
            return jsonify({"hits": []})
        # The reading page passes the already-resolved tree (what the store was
        # built with); resolve it from the document only if a caller omits it.
        if not tree:
            try:
                path = safe_path(root, rel)
            except (ValueError, FileNotFoundError):
                abort(404)
            tree = Document(str(path)).default_tree
        hits = vectors.similar(rel, tree, ref)
        # Enrich each neighbour with a highlighted excerpt + shared content words
        # from the FTS index (the vector store holds no text).  Best-effort:
        # skipped when FTS is off.
        if hits and fts_on:
            conn = fts_connect(db_path)
            if conn is not None:
                ctx = fts_similarity_context(
                    conn, (rel, tree, ref),
                    [(h["file"], h["tree"], h["ref"]) for h in hits])
                conn.close()
                for h in hits:
                    c = ctx.get((h["file"], h["tree"], h["ref"]), {})
                    h["snip"] = c.get("snip", "")
                    h["excerpt"] = c.get("excerpt", "")
                    h["shared"] = c.get("shared", [])
        # Attach the URN (primary identifier) and a display title for linking.
        for h in hits:
            h["urn"] = urn_for(h["file"])
            h.setdefault("shared", [])
        return jsonify({"hits": hits})

    @app.route("/tei.css")
    def css():
        return send_file(CSS_FILE, mimetype="text/css")

    return app
