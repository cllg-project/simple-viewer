"""
Optional semantic ("Similar passages") search over the thinnest citation units.

Two clearly separated sides:

* **Serve side** — `VectorStore`.  Needs only the Python standard library plus the
  `sqliteai-vector` SQLite extension (https://github.com/sqliteai/sqlite-vector),
  whose prebuilt binary ships in the `sqliteai-vector` wheel.  No ML runtime, no
  numpy: a passage finds its neighbours by looking up its own stored vector and
  letting the extension run an (approximate, quantized) cosine KNN scan.  Everything
  lives in one `vectors.sqlite` file, so the store is portable by *copy + install*:
  copy the file to another machine, `pip install sqliteai-vector`, done.

* **Build side** — `Encoder` / `ensure_model` / `build_vectors`.  This is the only
  code that needs `onnxruntime` + `tokenizers` + `huggingface_hub` + `numpy`; they
  are imported *lazily* so that `import cllg_viewer.vectors` at serve time pulls in
  none of them.  Leaf passages are rendered in parallel (reusing the spawn pool +
  watchdog from `search.py`), then encoded in the parent process and stored.

Graceful degrade: if a host's Python `sqlite3` cannot load extensions (rare — some
minimal/Alpine builds), `VectorStore.open` logs and returns ``None`` and the feature
is simply off, exactly like FTS when its index has not been built.
"""
from __future__ import annotations

import importlib.resources
import logging
import os
import sqlite3
import sys
import threading
import unicodedata
from pathlib import Path
from typing import List, Optional

from .rendering import ROOT_DIR
from .search import (DEFAULT_TIMEOUT, _index_document_leaves, _run_render_pool)
from .corpus import iter_editions

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DEFAULT_VECTORS_DIR = Path(os.environ.get(
    "CLLG_VECTORS", str(ROOT_DIR / "var" / "vectors")))
DEFAULT_VECTORS_DB = DEFAULT_VECTORS_DIR / "vectors.sqlite"
DEFAULT_MODEL_ID = "Paulanerus/AncientGreekVariantSBERT-ONNX"
DEFAULT_MODEL_DIR = ROOT_DIR / "var" / "models" / "AncientGreekVariantSBERT-ONNX"

EMBED_DIM = 768
MAX_SEQ_LENGTH = 512
DEFAULT_BATCH_SIZE = 32
DEFAULT_TOPK = 10


# --------------------------------------------------------------------------- #
# Model preprocessing
# --------------------------------------------------------------------------- #
def strip_accents_and_lowercase(text: str) -> str:
    """The encoder model's REQUIRED preprocessing: NFD-decompose, drop combining
    marks (Unicode category Mn), lowercase.

    *Deliberate divergence* from the FTS subsystem, which is accent-SENSITIVE for
    Greek (see the "Greek search is accent-sensitive" invariant in CLAUDE.md and
    `betacode.to_greek`).  These are two different subsystems with two different
    correct behaviours — do NOT try to reconcile them.  AncientGreekVariantSBERT
    was trained on accent-stripped, lowercased input, so to get meaningful vectors
    we must feed it exactly that.
    """
    decomposed = unicodedata.normalize("NFD", text)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return no_marks.lower()


# --------------------------------------------------------------------------- #
# sqlite-vector extension loading
# --------------------------------------------------------------------------- #
def _load_vector_extension(conn: sqlite3.Connection) -> bool:
    """Load the bundled `sqliteai-vector` binary into `conn`; return success.

    Returns False (and logs) when the package is missing or this Python's sqlite3
    was built without extension-loading support — the caller then disables the
    feature rather than crashing.
    """
    try:
        ext = importlib.resources.files("sqlite_vector.binaries") / "vector"
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        log.warning("sqliteai-vector not installed (%s) — semantic search disabled. "
                    "Install it with: pip install sqliteai-vector", exc)
        return False
    try:
        conn.enable_load_extension(True)
        conn.load_extension(str(ext))
        conn.enable_load_extension(False)
        return True
    except (AttributeError, sqlite3.OperationalError) as exc:
        log.warning("cannot load the sqlite-vector extension (%s) — semantic search "
                    "disabled. This host's sqlite3 lacks extension-loading support.",
                    exc)
        return False


def _init_vector_column(conn: sqlite3.Connection, dim: int) -> None:
    """(Re)establish the extension's per-connection context for passages.embedding.

    `vector_init` is required *every time a connection is opened* (even read-only,
    even on a copied db): it reloads the column's metadata into the extension's
    in-memory context.  It is idempotent, so calling it on an already-built db is
    safe.
    """
    conn.execute(
        "SELECT vector_init('passages','embedding',?)",
        (f"type=FLOAT32,dimension={dim},distance=COSINE",))


# --------------------------------------------------------------------------- #
# Serve side: the read-only vector store (stdlib + extension only)
# --------------------------------------------------------------------------- #
class VectorStore:
    """Read-only neighbour lookup over a built `vectors.sqlite`.

    A SQLite connection (and the extension's per-connection context loaded by
    `vector_init`/`vector_quantize_preload`) cannot be shared across threads, so
    each thread lazily opens and reuses its own connection.  Under the intended
    gunicorn *sync* workers that is exactly one connection per worker process;
    threaded servers (the Flask dev server) get one per thread.
    """

    def __init__(self, db_path: Path, dim: int) -> None:
        self._db_path = str(db_path)
        self._dim = dim
        self._local = threading.local()

    @classmethod
    def open(cls, db_path: Path = DEFAULT_VECTORS_DB) -> Optional["VectorStore"]:
        """Open a built store, or return None.

        None is returned (feature off, no crash) when the db has not been built,
        when the extension cannot be loaded, or when the store's context cannot be
        initialised — mirroring `search.fts_connect`.  A probe connection both
        decides this and reads the stored dimension; per-thread connections are
        then created lazily on demand.
        """
        db_path = Path(db_path)
        if not db_path.is_file():
            return None
        conn = cls._connect(str(db_path))
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
            dim = int(row[0]) if row else EMBED_DIM
        except sqlite3.Error:
            dim = EMBED_DIM
        ok = cls._prepare(conn, dim, db_path)
        conn.close()
        return cls(db_path, dim) if ok else None

    @staticmethod
    def _connect(db_path: str) -> Optional[sqlite3.Connection]:
        """Open read-only and load the extension; None (logged) on failure."""
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.warning("cannot open vector store %s (%s)", db_path, exc)
            return None
        conn.row_factory = sqlite3.Row
        if not _load_vector_extension(conn):
            conn.close()
            return None
        return conn

    @staticmethod
    def _prepare(conn: sqlite3.Connection, dim: int, db_path) -> bool:
        """Re-establish the extension context and load the quantized index."""
        try:
            _init_vector_column(conn, dim)
            conn.execute("SELECT vector_quantize_preload('passages','embedding')")
            return True
        except sqlite3.Error as exc:
            log.warning("sqlite-vector init/preload failed for %s (%s) — "
                        "semantic search disabled", db_path, exc)
            return False

    def _conn(self) -> sqlite3.Connection:
        """This thread's connection, opened+prepared on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect(self._db_path)
            if conn is None or not self._prepare(conn, self._dim, self._db_path):
                if conn is not None:
                    conn.close()
                raise sqlite3.OperationalError("vector store connection unavailable")
            self._local.conn = conn
        return conn

    @property
    def count(self) -> int:
        try:
            return self._conn().execute(
                "SELECT count(*) FROM passages").fetchone()[0]
        except sqlite3.Error:
            return 0

    def similar(self, file: str, tree: str, ref: str, k: int = DEFAULT_TOPK,
                exclude_same_document: bool = False) -> List[dict]:
        """Nearest leaf passages to (file, tree, ref), excluding the passage itself.

        Returns dicts {file, tree, ref, title, author, score} ordered by descending
        cosine similarity (score = 1 - cosine distance).  Empty list when the
        passage has no stored vector.
        """
        try:
            conn = self._conn()
            qrow = conn.execute(
                "SELECT embedding FROM passages WHERE file=? AND tree=? AND ref=?",
                (file, tree, ref)).fetchone()
            if qrow is None:
                return []
            query_blob = qrow[0]
            # Over-fetch so dropping self (and optionally same-document rows) still
            # leaves k results.  vector_quantize_scan returns (id == rowid, distance).
            fetch = k + 1 + (64 if exclude_same_document else 0)
            rows = conn.execute(
                """SELECT p.file AS file, p.tree AS tree, p.ref AS ref,
                          p.title AS title, p.author AS author,
                          v.distance AS distance
                   FROM vector_quantize_scan('passages','embedding', ?, ?) v
                   JOIN passages p ON p.rowid = v.id
                   ORDER BY v.distance""",
                (query_blob, fetch)).fetchall()
        except sqlite3.Error as exc:
            log.warning("similar() query failed (%s)", exc)
            return []
        out: List[dict] = []
        for r in rows:
            if r["file"] == file and r["tree"] == tree and r["ref"] == ref:
                continue  # the passage itself
            if exclude_same_document and r["file"] == file:
                continue
            out.append({
                "file": r["file"], "tree": r["tree"], "ref": r["ref"],
                "title": r["title"], "author": r["author"],
                "score": 1.0 - float(r["distance"]),
            })
            if len(out) >= k:
                break
        return out


# --------------------------------------------------------------------------- #
# Build side: encode passages (lazy ML imports)
# --------------------------------------------------------------------------- #
class Encoder:
    """ONNX sentence encoder.  Imports onnxruntime/tokenizers/numpy lazily.

    `device` selects the ONNX Runtime execution provider:
      * "auto" (default) — CUDA if it is available (i.e. onnxruntime-gpu is
        installed and a usable GPU is present), otherwise CPU;
      * "cuda" — prefer CUDA, but still fall back to CPU with a warning;
      * "cpu"  — force CPU.
    Encoding is the only GPU-worthy work in the project, and it is build-time
    only — the serve path never constructs an Encoder.
    """

    def __init__(self, model_dir: Path = DEFAULT_MODEL_DIR,
                 device: str = "auto") -> None:
        import onnxruntime as ort  # lazy: build-time only
        from tokenizers import Tokenizer  # lazy

        model_dir = Path(model_dir)
        onnx_path = model_dir / "onnx" / "model.onnx"
        if not onnx_path.is_file():
            raise FileNotFoundError(
                f"model not found at {onnx_path}; run ensure_model() first")
        providers = self._select_providers(device, ort.get_available_providers())
        if "CUDAExecutionProvider" in providers and hasattr(ort, "preload_dlls"):
            # onnxruntime-gpu wheels are built against CUDA 12.x + cuDNN 9.x and
            # load those runtime libraries at session creation.  preload_dlls()
            # pulls them from the nvidia-*-cu12 pip wheels (see
            # requirements-vectors-gpu.txt), so GPU works without a system CUDA
            # install.  Harmless no-op if the libs aren't present.
            try:
                ort.preload_dlls()
            except Exception as exc:  # noqa: BLE001 - best effort, falls back to CPU
                log.warning("onnxruntime preload_dlls() failed (%s)", exc)
        self._sess = ort.InferenceSession(str(onnx_path), providers=providers)
        print(f"encoder using: {self._sess.get_providers()}", file=sys.stderr)
        self._input_names = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tok.enable_truncation(MAX_SEQ_LENGTH)
        self._tok.enable_padding()
        # Adaptive ceiling on the effective batch size, learned from GPU OOMs.
        # build_vectors feeds passages in ascending length order, so once a size
        # OOMs at some length every later (longer) batch would OOM too — we ratchet
        # this down on the first failure so consecutive batches skip the doomed
        # full-size attempt instead of re-failing it.  Monotonically non-increasing.
        self._max_batch: Optional[int] = None

    @staticmethod
    def _select_providers(device: str, available: list) -> list:
        """Resolve the requested device to an ONNX Runtime provider list.

        CUDAExecutionProvider only appears in `available` when onnxruntime-gpu is
        installed alongside a working CUDA stack; the plain onnxruntime wheel
        offers CPU only.
        """
        device = (device or "auto").lower()
        if device == "cpu":
            return ["CPUExecutionProvider"]
        if device == "cuda" and "CUDAExecutionProvider" not in available:
            log.warning("CUDA requested but CUDAExecutionProvider is unavailable "
                        "(install onnxruntime-gpu + a matching CUDA/cuDNN). "
                        "Falling back to CPU.")
        prefer = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                  if p in available]
        return prefer or ["CPUExecutionProvider"]

    @staticmethod
    def _is_oom(exc: Exception) -> bool:
        """Recognise a CUDA/arena out-of-memory failure from ONNX Runtime."""
        s = str(exc)
        return ("Failed to allocate memory" in s
                or "out of memory" in s.lower()
                or "CUDA_ERROR_OUT_OF_MEMORY" in s)

    def encode(self, texts, batch_size: int = DEFAULT_BATCH_SIZE):
        """Encode a list of strings into an (len(texts), EMBED_DIM) float32 array,
        L2-normalized.  Mean-pools over the attention mask (the model has no
        pooling/normalize layer in its ONNX graph).

        The effective batch size is capped by `self._max_batch` once a GPU OOM has
        taught us a smaller safe size; re-read every step so a cap lowered mid-call
        applies to the remaining batches too."""
        import numpy as np  # lazy

        out = []
        i, total = 0, len(texts)
        while i < total:
            size = batch_size if self._max_batch is None \
                else min(batch_size, self._max_batch)
            out.append(self._encode_chunk(texts[i:i + size]))
            i += size
        if not out:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        return np.vstack(out)

    def _encode_chunk(self, texts):
        """Encode one batch, auto-splitting on GPU OOM.

        Attention scales with batch × seq² and the tokenizer pads to the longest
        passage in the batch, so a single long passage can spike a big batch past
        GPU memory.  On an out-of-memory error we lower `self._max_batch` (so the
        next, longer batches start smaller instead of re-failing), then halve and
        retry this batch, down to a single passage.
        """
        import numpy as np  # lazy
        if not texts:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        # Already know a smaller size is needed? Split at the cap instead of
        # re-failing at the full size (matters inside the first batch's recursion).
        if self._max_batch is not None and len(texts) > self._max_batch:
            cut = self._max_batch
            return np.vstack([self._encode_chunk(texts[:cut]),
                              self._encode_chunk(texts[cut:])])
        try:
            return self._run_batch(texts)
        except Exception as exc:  # noqa: BLE001
            if len(texts) > 1 and self._is_oom(exc):
                mid = len(texts) // 2
                if self._max_batch is None or mid < self._max_batch:
                    log.warning("GPU out of memory on a batch of %d passages; "
                                "capping batch size to %d and retrying",
                                len(texts), mid)
                    self._max_batch = mid
                return np.vstack([self._encode_chunk(texts[:mid]),
                                  self._encode_chunk(texts[mid:])])
            raise

    def _run_batch(self, texts):
        """Tokenize, run the model, mean-pool and L2-normalize one batch."""
        import numpy as np  # lazy
        batch = [strip_accents_and_lowercase(t) for t in texts]
        encs = self._tok.encode_batch(batch)
        ids = np.asarray([e.ids for e in encs], dtype=np.int64)
        mask = np.asarray([e.attention_mask for e in encs], dtype=np.int64)
        feed = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        outputs = self._sess.run(None, feed)
        emb = outputs[0]
        if emb.ndim == 3:
            # token embeddings -> mean pool weighted by the attention mask
            m = mask.astype(np.float32)[..., None]
            summed = (emb * m).sum(axis=1)
            counts = np.clip(m.sum(axis=1), 1e-9, None)
            vecs = summed / counts
        else:
            # already pooled (insurance against a model that pools in-graph)
            vecs = emb.astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.clip(norms, 1e-12, None)
        return vecs.astype(np.float32)


def ensure_model(model_id: str = DEFAULT_MODEL_ID,
                 model_dir: Path = DEFAULT_MODEL_DIR) -> Path:
    """Download the ONNX model snapshot into `model_dir` if not already present."""
    model_dir = Path(model_dir)
    if (model_dir / "onnx" / "model.onnx").is_file() \
            and (model_dir / "tokenizer.json").is_file():
        return model_dir
    from huggingface_hub import snapshot_download  # lazy: build-time only

    print(f"downloading {model_id} -> {model_dir}", file=sys.stderr)
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model_id, local_dir=str(model_dir))
    return model_dir


_STAGING_INSERT = ("INSERT INTO _staging(file,tree,ref,title,author,text)"
                   " VALUES (?,?,?,?,?,?)")


def _stage_from_render(conn: sqlite3.Connection, root: Path, jobs: int,
                       timeout: int, skipped: List) -> int:
    """Phase 1 (default): render *leaf* passages across the spawn pool into the
    staging table.  Appends (file, reason) to `skipped`; returns rows staged."""
    from tqdm import tqdm

    tasks = [(str(p.relative_to(root)), str(p))
             for p in sorted(iter_editions(root))]
    total = len(tasks)
    counters = {"staged": 0}
    pbar = tqdm(total=total, desc="rendering", unit="doc")

    def on_skip(rel: str, reason: str) -> None:
        skipped.append((rel, reason))
        tqdm.write(f"skip {rel}: {reason}", file=sys.stderr)

    def consume(result) -> None:
        rel, error, rows = result
        if error:
            on_skip(rel, error)
        if rows:
            # rows are 7-tuples (…, text, leaf); _staging keeps the first six.
            conn.executemany(_STAGING_INSERT, [r[:6] for r in rows])
            counters["staged"] += len(rows)
            conn.commit()
        pbar.update(1)
        pbar.set_postfix(passages=counters["staged"], skipped=len(skipped))

    _run_render_pool(tasks, _index_document_leaves, jobs, timeout, consume, on_skip)
    pbar.close()
    return counters["staged"]


def _stage_from_fts(conn: sqlite3.Connection, fts_db: Path) -> int:
    """Phase 1 (reuse): copy leaf passages' already-rendered text out of an FTS
    index built by `browse.py index` — no Saxon, no dapytains navigation.

    The FTS text is produced by the very same `renderer.render(node, "text")` call
    that the render path would use, so the vectors are identical; this only avoids
    rendering the corpus a second time.
    """
    from tqdm import tqdm

    fts_db = Path(fts_db)
    if not fts_db.is_file():
        raise FileNotFoundError(f"FTS index not found: {fts_db}")
    print(f"reusing rendered text from {fts_db} (skipping Saxon re-render)…",
          file=sys.stderr)
    src = sqlite3.connect(f"file:{fts_db}?mode=ro", uri=True)
    staged = 0
    try:
        try:
            total = src.execute(
                "SELECT count(*) FROM passages WHERE leaf='1'").fetchone()[0]
        except sqlite3.OperationalError as exc:
            # An FTS index built before the `leaf` column existed.
            raise RuntimeError(
                f"{fts_db} has no 'leaf' column — rebuild the FTS index with this "
                "version (browse.py index) before using --from-fts") from exc
        cur = src.execute(
            "SELECT file,tree,ref,title,author,text FROM passages WHERE leaf='1'")
        pbar = tqdm(total=total, desc="staging (FTS)", unit="passage")
        while True:
            rows = cur.fetchmany(2000)
            if not rows:
                break
            conn.executemany(_STAGING_INSERT, rows)
            staged += len(rows)
            conn.commit()
            pbar.update(len(rows))
        pbar.close()
    finally:
        src.close()
    return staged


def build_vectors(root: Path, db_path: Path = DEFAULT_VECTORS_DB,
                  model_dir: Path = DEFAULT_MODEL_DIR, jobs: Optional[int] = None,
                  timeout: int = DEFAULT_TIMEOUT,
                  batch_size: int = DEFAULT_BATCH_SIZE,
                  device: str = "auto",
                  from_fts: Optional[Path] = None) -> int:
    """Build `vectors.sqlite`: collect leaf-passage text, encode it in the parent
    process, store vectors + metadata, then quantize for fast KNN.

    Three phases keep RAM flat and preserve the Saxon+spawn invariant:
      1. stage leaf-passage text — either by rendering the corpus across the spawn
         pool, or (when `from_fts` is given) by reusing the already-rendered text
         from an FTS index, which skips Saxon entirely;
      2. encode staged rows in batches in the parent and INSERT into `passages`
         (onnxruntime never enters the spawn workers — they stay pure renderers);
      3. quantize the embedding column and record build metadata.
    """
    jobs = jobs or os.cpu_count() or 1
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    for sidecar in (db_path.with_suffix(db_path.suffix + "-wal"),
                    db_path.with_suffix(db_path.suffix + "-shm")):
        if sidecar.exists():
            sidecar.unlink()

    conn = sqlite3.connect(db_path)
    if not _load_vector_extension(conn):
        conn.close()
        raise RuntimeError(
            "sqliteai-vector is required to build vectors. "
            "Install it with: pip install sqliteai-vector")
    conn.execute("CREATE TABLE _staging("
                 "file TEXT, tree TEXT, ref TEXT, title TEXT, author TEXT, text TEXT)")

    # ---- Phase 1: stage leaf-passage text ------------------------------------ #
    skipped: List = []
    if from_fts is not None:
        _stage_from_fts(conn, from_fts)
        source = f"FTS index {Path(from_fts).name}"
    else:
        _stage_from_render(conn, Path(root), jobs, timeout, skipped)
        source = "rendered corpus"

    # ---- Phase 2: encode staged rows in the parent -> passages --------------- #
    n = conn.execute("SELECT count(*) FROM _staging").fetchone()[0]
    conn.execute("CREATE TABLE passages("
                 "file TEXT, tree TEXT, ref TEXT, title TEXT, author TEXT,"
                 " embedding BLOB)")
    _init_vector_column(conn, EMBED_DIM)
    conn.commit()

    from tqdm import tqdm

    encoder = Encoder(model_dir, device=device)
    done = 0
    pbar = tqdm(total=n, desc="encoding", unit="passage")
    # Stream the staged rows ONCE, sorted by text length, through a single cursor on
    # a separate read connection.  WAL lets us keep that read transaction open while
    # we INSERT into `passages` on `conn`.  Ordering by length groups similar-length
    # passages so the tokenizer pads less (faster, fewer GPU OOMs); rowid breaks
    # ties.  This replaces LIMIT/OFFSET paging, which re-sorted the whole table per
    # batch and degraded to O(N^2) as the offset grew.  Insertion order into
    # `passages` is irrelevant — the store maps rowid -> metadata.
    conn.execute("PRAGMA journal_mode=WAL")
    reader = sqlite3.connect(db_path)
    try:
        cur = reader.execute(
            "SELECT file,tree,ref,title,author,text FROM _staging"
            " ORDER BY length(text), rowid")
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            vecs = encoder.encode([r[5] for r in rows], batch_size=batch_size)
            conn.executemany(
                "INSERT INTO passages(file,tree,ref,title,author,embedding)"
                " VALUES (?,?,?,?,?,?)",
                [(r[0], r[1], r[2], r[3], r[4], vecs[i].tobytes())
                 for i, r in enumerate(rows)])
            conn.commit()
            done += len(rows)
            pbar.update(len(rows))
    finally:
        reader.close()
    pbar.close()

    # ---- Phase 3: quantize + record metadata --------------------------------- #
    conn.execute("SELECT vector_quantize('passages','embedding')")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value)")
    conn.executemany("INSERT INTO meta(key,value) VALUES (?,?)", [
        ("dim", EMBED_DIM), ("count", done), ("model", DEFAULT_MODEL_ID),
        ("distance", "COSINE"), ("quantized", 1),
    ])
    conn.execute("DROP TABLE _staging")
    conn.commit()
    # Return to a rollback journal so the shipped file is a single, portable
    # vectors.sqlite with no -wal/-shm sidecars (checkpoints + removes the WAL).
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()

    print(f"vectorized {done} leaf passages from the {source} -> {db_path}")
    if skipped:
        report = db_path.with_suffix(".skipped.tsv")
        with report.open("w", encoding="utf-8") as fh:
            fh.write("file\treason\n")
            for rel, reason in skipped:
                fh.write(f"{rel}\t{reason}\n")
        print(f"skipped {len(skipped)} document(s) -> {report}", file=sys.stderr)
    return 0
