"""
Tests for the optional semantic "Similar passages" store (cllg_viewer.vectors).

Split into three tiers by dependency weight:

* always-on, no deps      -> strip_accents_and_lowercase.
* needs the sqlite-vector extension (but no ML deps) -> a hand-built tiny store
  exercised through VectorStore + the /passage route.  Skipped if this Python's
  sqlite3 cannot load the extension.
* needs ML deps + the model -> Encoder smoke test.  Skipped otherwise.
"""
import os
import struct
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cllg_viewer as browse  # noqa: E402
from cllg_viewer import vectors as V  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "fixtures", "corpus", "data")
SAMPLE = "tlg1326/tlg001/tlg1326.tlg001.cllg-grc1.xml"
DIM = 8


def _extension_available() -> bool:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    try:
        return V._load_vector_extension(conn)
    finally:
        conn.close()


HAVE_EXT = _extension_available()
needs_ext = pytest.mark.skipif(
    not HAVE_EXT, reason="sqlite-vector extension not loadable on this host")


def _vec(*head):
    """An 8-d float vector from its leading components (rest zero)."""
    v = list(head) + [0.0] * (DIM - len(head))
    return v[:DIM]


def _build_store(db_path, file, tree, rows):
    """rows: list of (ref, vector). Build a minimal vectors.sqlite via the real
    module helpers so we exercise the exact storage format VectorStore reads."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    assert V._load_vector_extension(conn)
    conn.execute("CREATE TABLE passages("
                 "file TEXT, tree TEXT, ref TEXT, title TEXT, author TEXT,"
                 " embedding BLOB)")
    V._init_vector_column(conn, DIM)
    for ref, vec in rows:
        conn.execute(
            "INSERT INTO passages VALUES (?,?,?,?,?,?)",
            (file, tree, ref, f"Work of {file}", "Pseudo-Author",
             struct.pack("%df" % DIM, *vec)))
    conn.execute("SELECT vector_quantize('passages','embedding')")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value)")
    conn.executemany("INSERT INTO meta(key,value) VALUES (?,?)",
                     [("dim", DIM), ("count", len(rows))])
    conn.commit()
    conn.close()


def _fixture_leaf_refs(n=4):
    from pathlib import Path
    from dapytains.tei.document import Document
    doc = Document(str(Path(DATA, SAMPLE)))
    tree = doc.default_tree
    leaves = [u.ref for u in browse.walk_reffs(doc.get_reffs(tree))
              if not u.children]
    return tree, leaves[:n]


# --------------------------------------------------------------------------- #
# always-on: preprocessing
# --------------------------------------------------------------------------- #
class TestPreprocessing:
    def test_strips_greek_accents_and_lowercases(self):
        # The model's required preprocessing: NFD-strip diacritics + lowercase.
        assert browse.strip_accents_and_lowercase("Λόγος") == "λογος"
        assert browse.strip_accents_and_lowercase("ἄλλο") == "αλλο"

    def test_idempotent_on_plain_ascii(self):
        assert browse.strip_accents_and_lowercase("Hello") == "hello"


# --------------------------------------------------------------------------- #
# needs the extension (no ML deps)
# --------------------------------------------------------------------------- #
@needs_ext
class TestVectorStore:
    def _store(self, tmp_path):
        tree, refs = _fixture_leaf_refs(4)
        # ref[0] ~ ref[1] (close), ref[2]/ref[3] orthogonal (far).
        rows = [
            (refs[0], _vec(1.0, 0.0, 0.0)),
            (refs[1], _vec(0.95, 0.05, 0.0)),
            (refs[2], _vec(0.0, 1.0, 0.0)),
            (refs[3], _vec(0.0, 0.0, 1.0)),
        ]
        db = tmp_path / "vectors.sqlite"
        _build_store(db, SAMPLE, tree, rows)
        return db, tree, refs

    def test_open_count(self, tmp_path):
        db, _tree, _refs = self._store(tmp_path)
        store = V.VectorStore.open(db)
        assert store is not None
        assert store.count == 4

    def test_open_missing_returns_none(self, tmp_path):
        assert V.VectorStore.open(tmp_path / "nope.sqlite") is None

    def test_similar_excludes_self_and_orders_by_score(self, tmp_path):
        db, tree, refs = self._store(tmp_path)
        store = V.VectorStore.open(db)
        hits = store.similar(SAMPLE, tree, refs[0], k=3)
        assert [h["ref"] for h in hits] == [refs[1], refs[2], refs[3]] \
            or hits[0]["ref"] == refs[1]          # nearest first
        assert refs[0] not in [h["ref"] for h in hits]  # self excluded
        scores = [h["score"] for h in hits]
        assert scores == sorted(scores, reverse=True)   # descending similarity
        assert set(hits[0]) == {"file", "tree", "ref", "title", "author", "score"}

    def test_similar_unknown_ref_returns_empty(self, tmp_path):
        db, tree, _refs = self._store(tmp_path)
        store = V.VectorStore.open(db)
        assert store.similar(SAMPLE, tree, "nonexistent") == []

    def test_exclude_same_document(self, tmp_path):
        db, tree, refs = self._store(tmp_path)
        store = V.VectorStore.open(db)
        # everything is in SAMPLE, so excluding the same document empties results.
        assert store.similar(SAMPLE, tree, refs[0], exclude_same_document=True) == []


# --------------------------------------------------------------------------- #
# needs the extension: the /passage route
# --------------------------------------------------------------------------- #
@needs_ext
class TestPassageSimilarPanel:
    def _client(self, tmp_path, enable):
        from pathlib import Path
        tree, refs = _fixture_leaf_refs(4)
        db = tmp_path / "vectors.sqlite"
        _build_store(db, SAMPLE, tree, [
            (refs[0], _vec(1.0, 0.0)),
            (refs[1], _vec(0.9, 0.1)),
            (refs[2], _vec(0.0, 1.0)),
            (refs[3], _vec(0.0, 0.0, 1.0)),
        ])
        app = browse.create_app(Path(DATA).resolve(),
                                enable_vectors=enable, vectors_db=db)
        app.testing = True
        return app.test_client(), tree, refs

    def test_button_present_when_enabled(self, tmp_path):
        client, tree, refs = self._client(tmp_path, enable=True)
        r = client.get("/passage?file=" + urllib.parse.quote(SAMPLE)
                       + "&ref=" + urllib.parse.quote(refs[0]))
        body = r.get_data(as_text=True)
        assert 'id="similar-run"' in body           # the panel's run button
        assert 'id="similar-link"' in body          # the toolbar jump link
        assert 'similar.js' in body                 # its script
        assert 'data-ref="' + refs[0] + '"' in body  # passes the resolved passage

    def test_button_absent_when_disabled(self, tmp_path):
        client, _tree, refs = self._client(tmp_path, enable=False)
        r = client.get("/passage?file=" + urllib.parse.quote(SAMPLE)
                       + "&ref=" + urllib.parse.quote(refs[0]))
        body = r.get_data(as_text=True)
        assert 'id="similar-run"' not in body
        assert 'class="similar' not in body

    def test_similar_endpoint_returns_neighbours(self, tmp_path):
        client, tree, refs = self._client(tmp_path, enable=True)
        r = client.get("/similar?file=" + urllib.parse.quote(SAMPLE)
                       + "&ref=" + urllib.parse.quote(refs[0])
                       + "&tree=" + urllib.parse.quote(tree))
        data = r.get_json()
        hits = data["hits"]
        assert refs[0] not in [h["ref"] for h in hits]          # excludes self
        assert hits[0]["ref"] == refs[1]                        # nearest first
        assert {"file", "tree", "ref", "title", "author", "score", "urn"} \
            <= set(hits[0])

    def test_similar_endpoint_empty_when_disabled(self, tmp_path):
        client, tree, refs = self._client(tmp_path, enable=False)
        r = client.get("/similar?file=" + urllib.parse.quote(SAMPLE)
                       + "&ref=" + urllib.parse.quote(refs[0])
                       + "&tree=" + urllib.parse.quote(tree))
        assert r.get_json() == {"hits": []}

    def test_similar_endpoint_enriched_with_score_and_fts_excerpt(self, tmp_path):
        from pathlib import Path
        tree, refs = _fixture_leaf_refs(4)
        vdb = tmp_path / "vectors.sqlite"
        _build_store(vdb, SAMPLE, tree, [
            (refs[0], _vec(1.0, 0.0)),
            (refs[1], _vec(0.9, 0.1)),
            (refs[2], _vec(0.0, 1.0)),
            (refs[3], _vec(0.0, 0.0, 1.0)),
        ])
        fts = tmp_path / "search.sqlite"
        browse.build_index(Path(DATA).resolve(), fts)
        app = browse.create_app(Path(DATA).resolve(), db_path=fts, enable_fts=True,
                                enable_vectors=True, vectors_db=vdb)
        app.testing = True
        r = app.test_client().get("/similar?file=" + urllib.parse.quote(SAMPLE)
                                  + "&ref=" + urllib.parse.quote(refs[0])
                                  + "&tree=" + urllib.parse.quote(tree))
        hits = r.get_json()["hits"]
        assert hits
        assert all(isinstance(h["score"], (int, float)) for h in hits)
        assert any(h.get("excerpt") for h in hits)   # at least one excerpt from FTS


# --------------------------------------------------------------------------- #
# needs ML deps + the model
# --------------------------------------------------------------------------- #
class TestAdaptiveBatch:
    """Encoder lowers its batch ceiling on GPU OOM and keeps it for later calls
    (no ML deps: bypass __init__ and stub the model forward pass)."""

    def _encoder(self, safe):
        np = pytest.importorskip("numpy")
        from cllg_viewer.vectors import Encoder, EMBED_DIM
        enc = Encoder.__new__(Encoder)
        enc._max_batch = None
        calls = []

        def fake_run_batch(texts):
            calls.append(len(texts))
            if len(texts) > safe:
                raise RuntimeError("Failed to allocate memory for requested buffer")
            return np.ones((len(texts), EMBED_DIM), dtype=np.float32)

        enc._run_batch = fake_run_batch
        return enc, calls

    def test_cap_ratchets_and_sticks(self):
        enc, calls = self._encoder(safe=4)
        # first call discovers the cap...
        v = enc.encode([f"t{i}" for i in range(16)], batch_size=16)
        assert v.shape[0] == 16
        assert enc._max_batch == 4
        assert max(calls) == 16 and calls.count(16) == 1   # tried big once
        # ...later calls never re-attempt an oversized batch
        calls.clear()
        enc.encode([f"u{i}" for i in range(16)], batch_size=16)
        assert calls == [4, 4, 4, 4]

    def test_non_oom_error_propagates(self):
        np = pytest.importorskip("numpy")
        enc, _ = self._encoder(safe=99)
        enc._run_batch = lambda texts: (_ for _ in ()).throw(ValueError("boom"))
        with pytest.raises(ValueError):
            enc.encode(["a", "b"], batch_size=2)


@needs_ext
class TestFromFts:
    def test_vectorize_reuses_fts_leaf_text(self, tmp_path):
        pytest.importorskip("onnxruntime")
        pytest.importorskip("numpy")
        if not (V.DEFAULT_MODEL_DIR / "onnx" / "model.onnx").is_file():
            pytest.skip("model not downloaded (run: browse.py vectorize)")
        import sqlite3
        from pathlib import Path
        root = Path(DATA, "tlg1326").resolve()
        fts = tmp_path / "search.sqlite"
        browse.build_index(root, fts)
        # The FTS index holds containers AND leaves; --from-fts must take only the
        # leaves (leaf='1') — i.e. fewer rows than the full index.
        c = sqlite3.connect(fts)
        total = c.execute("SELECT count(*) FROM passages").fetchone()[0]
        leaves = c.execute("SELECT count(*) FROM passages WHERE leaf='1'").fetchone()[0]
        c.close()
        assert 0 < leaves < total
        vdb = tmp_path / "vectors.sqlite"
        browse.build_vectors(root, vdb, from_fts=fts)
        store = V.VectorStore.open(vdb)
        assert store is not None and store.count == leaves


class TestEncoder:
    def test_encode_shapes_and_similarity(self):
        pytest.importorskip("onnxruntime")
        pytest.importorskip("tokenizers")
        np = pytest.importorskip("numpy")
        if not (V.DEFAULT_MODEL_DIR / "onnx" / "model.onnx").is_file():
            pytest.skip("model not downloaded (run: browse.py vectorize)")
        enc = V.Encoder(V.DEFAULT_MODEL_DIR)
        vecs = enc.encode(["λόγος", "λόγος", "ἄλλο"])
        assert vecs.shape == (3, V.EMBED_DIM)
        assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-3)
        # identical inputs more similar than a different word
        assert float(vecs[0] @ vecs[1]) > float(vecs[0] @ vecs[2])
