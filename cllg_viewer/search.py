"""
Optional self-contained full-text search engine (SQLite FTS5, stdlib only) and
the parallel index builder that feeds it.

Rendering each passage (dapytains + Saxon XSLT) is CPU-bound and independent per
document, so the builder fans the documents out across a process pool. Each
worker builds its own Saxon processor once (in the pool initializer) — that is
the safe way to use saxonche across processes (see _worker_init / the "spawn"
note in build_index); we never share a processor across processes.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from dapytains.tei.document import Document

from .corpus import iter_editions, read_meta, walk_reffs
from .rendering import Renderer

# Default seconds to wait for *any* worker result before deciding the remaining
# in-flight documents are wedged (CPU spin / native crash) and giving up on them.
DEFAULT_TIMEOUT = 120


# --------------------------------------------------------------------------- #
# Read side: query the built index
# --------------------------------------------------------------------------- #
def fts_connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open the FTS index read-only; return None if it has not been built."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# Default page size for full-text results.
FTS_PAGE_SIZE = 8


def fts_query_string(raw: str) -> str:
    """Turn free user input into a safe FTS5 MATCH expression (AND of terms).

    Matching is case-insensitive (the `unicode61` tokenizer case-folds both the
    stored text and the query).  A trailing ``*`` on a term is kept as an FTS5
    prefix wildcard, so ``λογ*`` matches λόγος, λόγον, …  (FTS5 only supports
    prefix wildcards, not infix/suffix ones.)
    """
    terms = []
    for tok in raw.split():
        words = re.findall(r"\w+", tok, flags=re.UNICODE)
        if not words:
            continue
        # Keep the leading word of the token; honour a trailing '*' as a prefix.
        term = f'"{words[0]}"'
        if tok.endswith("*"):
            term += "*"
        terms.append(term)
    return " AND ".join(terms)


def fts_count(conn: sqlite3.Connection, raw: str) -> int:
    """Total number of passages matching `raw` (for pagination)."""
    match = fts_query_string(raw)
    if not match:
        return 0
    return conn.execute(
        "SELECT count(*) FROM passages WHERE passages MATCH ?", (match,)
    ).fetchone()[0]


def fts_excerpts(conn: sqlite3.Connection, keys, length: int = 240) -> dict:
    """Map each (file, tree, ref) key -> a short plain-text excerpt from the FTS
    index's stored passage text.  One table scan over the involved files (the FTS
    addressing columns are UNINDEXED), which is fine for the handful of keys a
    "Similar passages" lookup produces."""
    keys = list(keys)
    files = sorted({k[0] for k in keys})
    if not files:
        return {}
    placeholders = ",".join("?" * len(files))
    rows = conn.execute(
        f"SELECT file, tree, ref, text FROM passages WHERE file IN ({placeholders})",
        files).fetchall()
    wanted = set(keys)
    out: dict = {}
    for file, tree, ref, text in rows:
        if (file, tree, ref) in wanted and text:
            collapsed = " ".join(text.split())
            out[(file, tree, ref)] = (collapsed if len(collapsed) <= length
                                      else collapsed[:length].rstrip() + "…")
    return out


def _norm_token(word: str) -> str:
    """Accent-strip + lowercase a token for lexical overlap (NOT search — this is
    a display affinity feature, so being lenient about case/accents is fine)."""
    import unicodedata
    decomposed = unicodedata.normalize("NFD", word)
    return "".join(c for c in decomposed
                   if unicodedata.category(c) != "Mn").lower()


def fts_similarity_context(conn: sqlite3.Connection, query_key, hits,
                           length: int = 240, min_len: int = 4,
                           max_shared: int = 6) -> dict:
    """For a "Similar passages" result set, compute per-hit display context from
    the FTS index: a highlighted excerpt (`snip`) and the list of `shared` content
    words it has in common with the query passage.

    `query_key` and each item of `hits` are (file, tree, ref) tuples.  Returns
    {hit_key: {"snip": html, "shared": [words], "excerpt": text}}.
    """
    import html as _html

    keys = [query_key, *hits]
    files = sorted({k[0] for k in keys})
    if not files:
        return {}
    placeholders = ",".join("?" * len(files))
    rows = conn.execute(
        f"SELECT file, tree, ref, text FROM passages WHERE file IN ({placeholders})",
        files).fetchall()
    text_by_key = {(f, t, r): (txt or "") for f, t, r, txt in rows}

    query_text = text_by_key.get(query_key, "")
    qnorm = {n for n in (_norm_token(w)
                         for w in re.findall(r"\w+", query_text, re.UNICODE))
             if len(n) >= min_len}

    out: dict = {}
    for key in hits:
        text = text_by_key.get(key, "")
        if not text:
            out[key] = {"snip": "", "shared": [], "excerpt": ""}
            continue
        collapsed = " ".join(text.split())
        excerpt = (collapsed if len(collapsed) <= length
                   else collapsed[:length].rstrip() + "…")
        # Highlight shared tokens inside the excerpt and collect surface forms.
        pieces, shared, seen, pos = [], [], set(), 0
        for m in re.finditer(r"\w+", excerpt, re.UNICODE):
            pieces.append(_html.escape(excerpt[pos:m.start()]))
            surface = m.group(0)
            norm = _norm_token(surface)
            if len(norm) >= min_len and norm in qnorm:
                pieces.append("<mark>" + _html.escape(surface) + "</mark>")
                if norm not in seen:
                    seen.add(norm)
                    shared.append(surface)
            else:
                pieces.append(_html.escape(surface))
            pos = m.end()
        pieces.append(_html.escape(excerpt[pos:]))
        out[key] = {"snip": "".join(pieces), "excerpt": excerpt,
                    "shared": shared[:max_shared]}
    return out


def fts_search(conn: sqlite3.Connection, raw: str,
               limit: int = FTS_PAGE_SIZE, offset: int = 0) -> List[dict]:
    match = fts_query_string(raw)
    if not match:
        return []
    rows = conn.execute(
        """SELECT file, tree, ref, title, author,
                  snippet(passages, 5, '<mark>', '</mark>', ' … ', 12) AS snippet
           FROM passages
           WHERE passages MATCH ?
           ORDER BY rank
           LIMIT ? OFFSET ?""",
        (match, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Write side: build the index (parallel, one worker per document)
# --------------------------------------------------------------------------- #
_WORKER_RENDERER: Optional["Renderer"] = None

# A row destined for the index: (file, tree, ref, title, author, text, leaf).
# `leaf` (a bool) marks the thinnest citation units; it lets `vectorize --from-fts`
# pull exactly the leaf passages' already-rendered text out of the FTS index
# instead of re-running Saxon.  FTS itself indexes all rows regardless.
_Row = Tuple[str, str, str, str, str, str, bool]


def _worker_init() -> None:
    global _WORKER_RENDERER
    _WORKER_RENDERER = Renderer()


def _render_document(task: Tuple[str, str],
                     leaves_only: bool) -> Tuple[str, Optional[str], List[_Row]]:
    """Render the passages of one document (shared body of the two workers below).

    Returns (rel, error, rows).  `error` is a short reason string when the
    document could not be (fully) processed, else None.  A worker must NEVER
    raise: some corpus files have a citeStructure dapytains cannot handle (e.g.
    unit names that compile to an invalid regex group), and one bad file must not
    abort the whole pool — it is skipped and reported instead.

    When `leaves_only` is True only the thinnest citation units (those with no
    children) are emitted — that is what semantic vectorization wants, one vector
    per leaf passage.  Otherwise every unit in the tree is emitted (FTS indexes
    containers too).

    Note: a worker that *raises* is handled here, but a worker that *hangs* (a
    C-level regex backtrack or Saxon spin) or *segfaults* never returns at all;
    that case is caught by the no-progress watchdog in _run_render_pool.
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
            if leaves_only and unit.children:
                continue
            try:
                node = document.get_passage(ref_or_start=unit.ref, tree=tree)
                text = renderer.render(node, "text").strip()
            except Exception:  # noqa: BLE001 - skip the individual passage
                continue
            if text:
                rows.append((rel, tree, unit.ref, meta["title"], meta["author"],
                             text, not unit.children))
    return rel, ("; ".join(errors) if errors else None), rows


def _index_document(task: Tuple[str, str]) -> Tuple[str, Optional[str], List[_Row]]:
    """Render *every* passage of one document (FTS).  Top-level for spawn pickling."""
    return _render_document(task, leaves_only=False)


def _index_document_leaves(task: Tuple[str, str]) -> Tuple[str, Optional[str], List[_Row]]:
    """Render only the *leaf* passages of one document (semantic vectors).

    Top-level (module-scope) so it is picklable for the spawn pool.
    """
    return _render_document(task, leaves_only=True)


def _run_render_pool(tasks: List[Tuple[str, str]],
                     worker_fn: Callable[[Tuple[str, str]], tuple],
                     jobs: int, timeout: int,
                     on_result: Callable[[tuple], None],
                     on_skip: Callable[[str, str], None]) -> None:
    """Fan documents out across a spawn process pool — the single home of the
    spawn-only + no-progress-watchdog invariant shared by FTS indexing and
    semantic vectorization.

    `worker_fn` must be a module-level callable (picklable for spawn) whose
    result tuple begins with the document's rel path.  `on_result(result)` is
    called for each returned result in completion order; `on_skip(rel, reason)`
    for documents whose worker wedged (never returned within `timeout`).
    """
    total = len(tasks)
    if jobs == 1 or total <= 1:
        _worker_init()
        for task in tasks:
            on_result(worker_fn(task))
        return
    # Use the "spawn" start method: each worker is a *fresh* process that builds
    # its own Saxon processor in the initializer.  SaxonC starts native threads,
    # and forking a multi-threaded parent risks deadlocks, so we deliberately
    # avoid fork here.  maxtasksperchild recycles workers to bound native memory
    # growth over thousands of documents.
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=jobs, initializer=_worker_init,
                  maxtasksperchild=50) as pool:
        # No-progress watchdog: a worker stuck in a C-level regex backtrack or
        # Saxon spin (or one that segfaults) never puts its result on the queue,
        # and multiprocessing.Pool cannot recover that lost task — a plain
        # `for ... in imap_unordered` would then block forever.  So we pull
        # results with a timeout: as long as *any* worker keeps producing we keep
        # going; only when nothing returns within `timeout` do we conclude the
        # in-flight documents are wedged, terminate the pool, and record the
        # un-returned files as skipped.
        #
        # (signal.alarm is not used: SIGALRM only raises at Python bytecode
        # boundaries, so a native CPU spin would ignore it.)
        done_rels: set[str] = set()
        it = pool.imap_unordered(worker_fn, tasks, chunksize=1)
        for _ in range(total):
            try:
                result = it.next(timeout=timeout)
            except mp.TimeoutError:
                pool.terminate()
                reason = (f"timeout: stuck in dapytains/Saxon "
                          f"(no result in {timeout}s)")
                for rel, _path in tasks:
                    if rel not in done_rels:
                        on_skip(rel, reason)
                break
            done_rels.add(result[0])
            on_result(result)


def build_index(root: Path, db_path: Path, jsonl: Optional[Path] = None,
                jobs: Optional[int] = None, timeout: int = DEFAULT_TIMEOUT) -> int:
    jobs = jobs or os.cpu_count() or 1
    tasks = [(str(p.relative_to(root)), str(p)) for p in sorted(iter_editions(root))]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    # unicode61 case-folds and (remove_diacritics 2) folds Latin diacritics, so the
    # Latin corpus searches accent-insensitively.  Note: SQLite's tokenizer does NOT
    # fold Greek accents, so Greek search is accent-sensitive (type the accents).
    # `leaf` ('1'/'0', UNINDEXED) lets `vectorize --from-fts` select only the
    # thinnest citation units' already-rendered text and skip Saxon.
    conn.execute(
        """CREATE VIRTUAL TABLE passages USING fts5(
               file UNINDEXED, tree UNINDEXED, ref UNINDEXED,
               title, author, text, leaf UNINDEXED,
               tokenize = 'unicode61 remove_diacritics 2')""")

    from tqdm import tqdm

    jh = jsonl.open("w", encoding="utf-8") if jsonl else None
    if jsonl:
        jsonl.parent.mkdir(parents=True, exist_ok=True)
    n_docs = n_passages = 0
    total = len(tasks)
    skipped: List[Tuple[str, str]] = []  # (file, reason) for the skip report
    pbar = tqdm(total=total, desc="indexing", unit="doc")

    def on_skip(rel: str, reason: str) -> None:
        skipped.append((rel, reason))
        tqdm.write(f"skip {rel}: {reason}", file=sys.stderr)

    def consume(result: Tuple[str, Optional[str], List[_Row]]) -> None:
        nonlocal n_docs, n_passages
        rel, error, rows = result
        if error:
            on_skip(rel, error)
        if rows:
            n_docs += 1
            # FTS5 columns are textual, so store leaf as '1'/'0' (query: leaf='1').
            conn.executemany(
                "INSERT INTO passages(file, tree, ref, title, author, text, leaf)"
                " VALUES (?,?,?,?,?,?,?)",
                [(r[0], r[1], r[2], r[3], r[4], r[5], "1" if r[6] else "0")
                 for r in rows])
            if jh:
                for rel_, tree, ref, title, author, text, leaf in rows:
                    jh.write(json.dumps(
                        {"file": rel_, "tree": tree, "ref": ref,
                         "title": title, "author": author, "text": text,
                         "leaf": leaf},
                        ensure_ascii=False) + "\n")
            n_passages += len(rows)
            conn.commit()
        pbar.update(1)
        pbar.set_postfix(passages=n_passages, skipped=len(skipped))

    try:
        _run_render_pool(tasks, _index_document, jobs, timeout, consume, on_skip)
    finally:
        pbar.close()
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
