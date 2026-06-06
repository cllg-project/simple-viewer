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
from typing import List, Optional, Tuple

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
# Write side: build the index (parallel, one worker per document)
# --------------------------------------------------------------------------- #
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

    Note: a worker that *raises* is handled here, but a worker that *hangs* (a
    C-level regex backtrack or Saxon spin) or *segfaults* never returns at all;
    that case is caught by the no-progress watchdog in build_index.
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
            # so we deliberately avoid fork here.  maxtasksperchild recycles workers
            # to bound native memory growth over thousands of documents.
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=jobs, initializer=_worker_init,
                          maxtasksperchild=50) as pool:
                # No-progress watchdog: a worker stuck in a C-level regex backtrack
                # or Saxon spin (or one that segfaults) never puts its result on the
                # queue, and multiprocessing.Pool cannot recover that lost task — a
                # plain `for ... in imap_unordered` would then block forever.  So we
                # pull results with a timeout: as long as *any* worker keeps
                # producing we keep going; only when nothing returns within `timeout`
                # do we conclude the in-flight documents are wedged, terminate the
                # pool, and record the un-returned files as skipped.
                #
                # (signal.alarm is not used: SIGALRM only raises at Python bytecode
                # boundaries, so a native CPU spin would ignore it.)
                done_rels: set[str] = set()
                it = pool.imap_unordered(_index_document, tasks, chunksize=1)
                for _ in range(total):
                    try:
                        result = it.next(timeout=timeout)
                    except mp.TimeoutError:
                        pool.terminate()
                        reason = (f"timeout: stuck in dapytains/Saxon "
                                  f"(no result in {timeout}s)")
                        for rel, _path in tasks:
                            if rel not in done_rels:
                                skipped.append((rel, reason))
                                print(f"skip {rel}: {reason}", file=sys.stderr)
                        break
                    done_rels.add(result[0])
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
