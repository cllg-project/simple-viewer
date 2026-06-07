"""
Tests for the presentation layer: xslt/tei-to-html.xsl, xslt/tei-to-text.xsl and
browse.py.

The stylesheets must be robust to receiving only *part* of a node (an excerpt),
so the fixtures deliberately feed bare subtrees with no <teiHeader>.  A single
Saxon processor is shared across the suite via a module fixture.
"""

import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The implementation now lives in the `cllg_viewer` package; the `browse` alias
# keeps every call site below unchanged.
import cllg_viewer as browse  # noqa: E402

TEI = "http://www.tei-c.org/ns/1.0"
# Self-contained fixture corpus (one small work) so the suite runs anywhere,
# including CI, without cloning the full corpus.
DATA = os.path.join(os.path.dirname(__file__), "fixtures", "corpus", "data")
SAMPLE = "tlg1326/tlg001/tlg1326.tlg001.cllg-grc1.xml"


@pytest.fixture(scope="module")
def renderer():
    return browse.Renderer()


def _render(renderer, fragment, mode="html"):
    from lxml import etree
    node = etree.fromstring(fragment.encode("utf-8"))
    return renderer.render(node, mode)


# A bare <div> passage (no TEI/teiHeader wrapper) exercising the tricky bits.
FRAGMENT = f"""<div xmlns="{TEI}" type="fragment" n="9">
  <head>Heading <hi rend="italic">x</hi></head>
  <l n="1">word
    <choice><corr>amavit</corr><sic>amaviit</sic></choice>
    <choice><expan>id est</expan><abbr>i.e.</abbr></choice>
    <note type="footnote">drop me</note>
    <foreign xml:lang="grc">λόγος</foreign>
    <hi rend="sup">2</hi></l>
</div>"""


class TestHtmlStylesheet:
    def test_renders_bare_subtree(self, renderer):
        """No <TEI>/<teiHeader> in the input -> still renders (robustness)."""
        html = _render(renderer, FRAGMENT)
        assert '<section class="tei-div"' in html
        assert 'data-type="fragment"' in html
        assert 'data-n="9"' in html

    def test_verse_line_with_gutter(self, renderer):
        html = _render(renderer, FRAGMENT)
        assert 'class="tei-l"' in html
        assert '<span class="tei-lineno">1</span>' in html

    def test_choice_shows_reading_and_keeps_variant(self, renderer):
        html = _render(renderer, FRAGMENT)
        assert "amavit" in html and "id est" in html      # reading text kept
        assert 'title="amaviit"' in html                  # variant in @title
        assert "amaviit" not in html.replace('title="amaviit"', "")  # not shown as text

    def test_rend_maps_to_semantic_html(self, renderer):
        html = _render(renderer, FRAGMENT)
        assert "<em class=\"tei-hi\">x</em>" in html       # rend=italic -> <em>
        assert "<sup class=\"tei-hi\">2</sup>" in html     # rend=sup -> <sup>

    def test_note_kept_in_html(self, renderer):
        html = _render(renderer, FRAGMENT)
        assert 'class="tei-note"' in html
        assert "drop me" in html

    def test_foreign_lang(self, renderer):
        html = _render(renderer, FRAGMENT)
        assert '<span class="tei-foreign" lang="grc">λόγος</span>' in html

    def test_unknown_element_falls_through(self, renderer):
        html = _render(renderer, f'<weird xmlns="{TEI}">kept</weird>')
        assert 'class="tei tei-weird"' in html
        assert "kept" in html


class TestTextStylesheet:
    def test_drops_notes(self, renderer):
        text = _render(renderer, FRAGMENT, "text")
        assert "drop me" not in text

    def test_keeps_reading_variant(self, renderer):
        text = _render(renderer, FRAGMENT, "text")
        assert "amavit" in text and "amaviit" not in text
        assert "id est" in text and "i.e." not in text

    def test_inline_words_do_not_merge(self, renderer):
        text = _render(renderer, FRAGMENT, "text")
        assert "id est λόγος" in text

    def test_blocks_are_newline_separated(self, renderer):
        text = _render(renderer, FRAGMENT, "text")
        lines = [l for l in text.splitlines() if l.strip()]
        assert lines[0] == "Heading x"


class TestBrowseApp:
    @pytest.fixture(scope="class")
    def client(self):
        from pathlib import Path
        app = browse.create_app(Path(DATA).resolve())
        app.testing = True
        return app.test_client()

    def test_index_lists_works(self, client):
        r = client.get("/")
        assert r.status_code == 200
        # URN is the primary identifier; the fixture work has one.
        assert b"/doc?urn=" in r.data

    def test_doc_lists_passages(self, client):
        # ?file= is still accepted as a fallback for addressing the work...
        r = client.get("/doc?file=" + urllib.parse.quote(SAMPLE))
        assert r.status_code == 200
        # ...but the links it emits are URN-keyed.
        assert b"/passage?urn=" in r.data

    def test_doc_addressable_by_urn(self, client):
        from cllg_viewer.corpus import dts_identifier
        from pathlib import Path
        urn = dts_identifier(Path(DATA, SAMPLE))
        assert urn
        r = client.get("/doc?urn=" + urllib.parse.quote(urn))
        assert r.status_code == 200
        assert b"/passage?urn=" in r.data

    def test_passage_html(self, client):
        r = client.get("/passage?file=" + urllib.parse.quote(SAMPLE) + "&ref=4")
        assert r.status_code == 200
        assert b"tei-l" in r.data
        assert "Γήρειαν".encode("utf-8") in r.data

    def test_passage_text_is_plain(self, client):
        r = client.get("/passage?file=" + urllib.parse.quote(SAMPLE) + "&ref=4&format=text")
        assert r.status_code == 200
        assert r.mimetype == "text/plain"

    def test_path_escape_blocked(self, client):
        r = client.get("/passage?file=../etc/passwd&ref=1")
        assert r.status_code == 404


class TestPager:
    def test_neighbors_middle(self):
        from dapytains.tei.document import Document
        from pathlib import Path
        flat = list(browse.walk_reffs(
            Document(str(Path(DATA, SAMPLE))).get_reffs("default")))
        prev, nxt = browse.passage_neighbors(flat, "4")
        assert prev == "3" and nxt == "5a"

    def test_neighbors_first_has_no_prev(self):
        from dapytains.tei.document import Document
        from pathlib import Path
        flat = list(browse.walk_reffs(
            Document(str(Path(DATA, SAMPLE))).get_reffs("default")))
        first = flat[0].ref
        prev, nxt = browse.passage_neighbors(flat, first)
        assert prev is None and nxt is not None

    def test_neighbors_whole_work(self):
        assert browse.passage_neighbors([], None) == (None, None)

    def test_passage_page_has_pager_links(self):
        from pathlib import Path
        app = browse.create_app(Path(DATA).resolve())
        app.testing = True
        r = app.test_client().get(
            "/passage?file=" + urllib.parse.quote(SAMPLE) + "&ref=4")
        body = r.get_data(as_text=True)
        assert 'class="prev"' in body and 'class="next"' in body
        assert "ref=3" in body and "ref=5a" in body
        assert body.count('nav class="pager"') == 2  # top and bottom


class TestCatalogCache:
    def test_catalog_scanned_once(self, monkeypatch):
        from pathlib import Path
        calls = {"n": 0}
        real = browse.read_meta

        def counting(path):
            calls["n"] += 1
            return real(path)

        # web.create_app imports read_meta into its own module namespace, so patch
        # it there (browse is the cllg_viewer package; browse.web is the submodule).
        monkeypatch.setattr(browse.web, "read_meta", counting)
        app = browse.create_app(Path(DATA).resolve())
        app.testing = True
        client = app.test_client()
        client.get("/")
        after_first = calls["n"]
        assert after_first > 0          # scanned the corpus
        client.get("/")
        assert calls["n"] == after_first  # second request served from cache


@pytest.fixture(scope="module")
def fts_db(tmp_path_factory):
    """Build a small real FTS index over one work for the search tests."""
    from pathlib import Path
    root = Path(DATA, "tlg1326").resolve()
    db = tmp_path_factory.mktemp("idx") / "search.sqlite"
    browse.build_index(root, db)
    return root, db


class TestParallelIndex:
    """The index builder fans documents out across a process pool."""

    def _two_doc_corpus(self, tmp_path):
        import shutil
        from pathlib import Path
        src = Path(DATA, SAMPLE)
        root = tmp_path / "data"
        for name in ("aaa0001", "bbb0002"):
            d = root / name / "w001"
            d.mkdir(parents=True)
            shutil.copy(src, d / f"{name}.w001.cllg-grc1.xml")
        return root

    def test_parallel_matches_serial(self, tmp_path):
        import sqlite3
        root = self._two_doc_corpus(tmp_path)
        db1, db2 = tmp_path / "s1.sqlite", tmp_path / "s2.sqlite"
        browse.build_index(root, db1, jobs=1)         # serial path
        browse.build_index(root, db2, jobs=2)         # process-pool path (2 tasks)
        q = "SELECT file, ref, text FROM passages"
        rows1 = set(sqlite3.connect(db1).execute(q))
        rows2 = set(sqlite3.connect(db2).execute(q))
        assert rows1 and rows1 == rows2


# A minimal TEI whose *nested* citeStructure accumulates a unit name with a hyphen
# ("chapter__column-or-fragment"), which dapytains feeds into a regex group name
# -> re.match raises PatternError inside get_reffs (it resolves the parent's xpath
# for the nested level).  Such files must be skipped and reported, never crash the
# run.  (Reproduces the real tlg0300/tlg0294 failures.)
_BROKEN_TEI = """<?xml version="1.0" encoding="utf-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader><fileDesc>
    <titleStmt><title>Broken</title><author>Anon</author></titleStmt>
    <publicationStmt><p>p</p></publicationStmt><sourceDesc><p>s</p></sourceDesc>
  </fileDesc><encodingDesc><refsDecl>
    <citeStructure match="/TEI/text/body/div" use="@n" unit="chapter">
      <citeStructure match="div" use="@n" unit="column-or-fragment"/>
    </citeStructure>
  </refsDecl></encodingDesc></teiHeader>
  <text><body>
    <div n="1"><div n="1"><l n="1">x</l></div></div>
  </body></text>
</TEI>"""


class TestResilientIndex:
    """A document dapytains cannot navigate is skipped, not fatal."""

    def test_broken_doc_skipped_others_indexed(self, tmp_path):
        import shutil
        import sqlite3
        from pathlib import Path
        root = tmp_path / "data"
        good = root / "good0001" / "w001"
        good.mkdir(parents=True)
        shutil.copy(Path(DATA, SAMPLE), good / "good0001.w001.cllg-grc1.xml")
        bad = root / "bad0001" / "w001"
        bad.mkdir(parents=True)
        (bad / "bad0001.w001.cllg-grc1.xml").write_text(_BROKEN_TEI, encoding="utf-8")

        db = tmp_path / "search.sqlite"
        assert browse.build_index(root, db, jobs=2) == 0  # must not raise/hang

        files = {f for (f,) in sqlite3.connect(db).execute(
            "SELECT DISTINCT file FROM passages")}
        assert any("good0001" in f for f in files)      # good doc indexed
        assert not any("bad0001" in f for f in files)   # broken doc absent

        report = db.with_suffix(".skipped.tsv")
        assert report.is_file()
        assert "bad0001" in report.read_text(encoding="utf-8")

    def test_worker_returns_error_not_raises(self, tmp_path):
        p = tmp_path / "bad.cllg-grc1.xml"
        p.write_text(_BROKEN_TEI, encoding="utf-8")
        browse._worker_init()
        rel, error, rows = browse._index_document(("bad.cllg-grc1.xml", str(p)))
        assert error is not None and rows == []


class TestNavigationSearch:
    """Always-on: search authors/works to navigate (no index required)."""

    @pytest.fixture(scope="class")
    def client(self):
        from pathlib import Path
        app = browse.create_app(Path(DATA).resolve())  # enable_fts defaults to False
        app.testing = True
        return app.test_client()

    def test_home_groups_by_author(self, client):
        r = client.get("/")
        assert b'<details class="author"' in r.data

    def test_search_box_present(self, client):
        assert b'name="q"' in client.get("/").data

    def test_author_work_filter(self, client):
        r = client.get("/?q=" + urllib.parse.quote("Dionysius"))
        assert b"<details" in r.data

    def test_no_fulltext_by_default(self, client):
        # Without --fulltext, a content-only term yields no passage hits.
        r = client.get("/?q=" + urllib.parse.quote("Βασσαρικά"))
        assert b"passage matches" not in r.data

    def test_text_scope_disabled_without_index(self, client):
        # The "Inside texts" radio is present but disabled when full-text is off.
        body = client.get("/").get_data(as_text=True)
        assert 'value="text"' in body and "disabled" in body


class TestOptionalFullText:
    """Opt-in full-text search engine (SQLite FTS5), enabled with enable_fts."""

    @pytest.fixture(scope="class")
    def client(self, fts_db):
        root, db = fts_db
        app = browse.create_app(root, db_path=db, enable_fts=True)
        app.testing = True
        return app.test_client()

    def test_index_builds_valid_fts(self, fts_db):
        import sqlite3
        _, db = fts_db
        assert db.is_file()
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT count(*) FROM passages").fetchone()[0] > 0

    def test_fulltext_search_greek(self, client):
        # Greek search is accent-sensitive (SQLite folds Latin, not Greek).
        # Full-text now lives behind the "Inside texts" field (scope=text).
        r = client.get("/?scope=text&q=" + urllib.parse.quote("Βασσαρικά"))
        body = r.get_data(as_text=True)
        assert "result-meta--top" in body   # results header rendered
        assert "<mark>" in body

    def test_meta_scope_does_not_run_fulltext(self, client):
        # The default "Works" field never runs the content engine.
        body = client.get("/?q=" + urllib.parse.quote("Βασσαρικά")).get_data(as_text=True)
        assert "result-meta--top" not in body

    def test_betacode_input_searches_greek(self, client):
        # Beta Code with the beta flag is converted server-side: Bassarika/
        # "*bassarika/" -> Βασσαρικά, which then matches the indexed text.
        r = client.get("/?scope=text&beta=1&q=" + urllib.parse.quote("*bassarika/"))
        body = r.get_data(as_text=True)
        assert "result-meta--top" in body
        assert "Βασσαρικά" in body          # the converted query is echoed back

    def test_fulltext_off_when_not_enabled(self, fts_db):
        # Same built index, but enable_fts=False -> text scope falls back to meta.
        root, db = fts_db
        app = browse.create_app(root, db_path=db, enable_fts=False)
        app.testing = True
        r = app.test_client().get("/?scope=text&q=" + urllib.parse.quote("Βασσαρικά"))
        assert b"result-meta--top" not in r.data

    def test_wildcard_prefix(self, client):
        # A trailing '*' is a prefix wildcard: Βασσαρικ* matches Βασσαρικά.
        r = client.get("/?scope=text&q=" + urllib.parse.quote("Βασσαρικ*"))
        body = r.get_data(as_text=True)
        assert "result-meta--top" in body
        assert "Βασσαρικά" in body          # the matched token, in the snippet

    def test_case_insensitive(self, client):
        # unicode61 case-folds: a lowercase query matches the capitalised token.
        r = client.get("/?scope=text&q=" + urllib.parse.quote("βασσαρικά"))
        assert "result-meta--top" in r.get_data(as_text=True)

    def test_pagination(self, client):
        # τ* matches many passages -> multiple pages, with a working pager.
        p1 = client.get("/?scope=text&q=" + urllib.parse.quote("τ*")
                        + "&page=1").get_data(as_text=True)
        p2 = client.get("/?scope=text&q=" + urllib.parse.quote("τ*")
                        + "&page=2").get_data(as_text=True)
        assert "Page <b>1</b>" in p1                      # "Page 1 of N" status
        assert 'class="pagination"' in p1                 # numbered controls
        assert "&amp;page=2" in p1 or "&page=2" in p1     # a page link
        assert "Showing <b>1–8</b>" in p1                 # 8 per page
        assert p1 != p2                                   # different results per page

    def test_pagination_clamps_overflow(self, client):
        # A page past the end clamps to the last page rather than erroring/empty.
        r = client.get("/?scope=text&q=" + urllib.parse.quote("Βασσαρικά")
                       + "&page=999")
        assert r.status_code == 200
        assert "result-meta--top" in r.get_data(as_text=True)


    def test_fts_excerpts(self, fts_db):
        _, db = fts_db
        conn = browse.fts_connect(db)
        row = conn.execute(
            "SELECT file, tree, ref FROM passages WHERE leaf='1' LIMIT 1").fetchone()
        key = (row[0], row[1], row[2])
        ex = browse.fts_excerpts(conn, [key], length=50)
        conn.close()
        assert key in ex and ex[key]                 # non-empty excerpt
        assert len(ex[key]) <= 51                     # truncated (+ ellipsis) or short


def test_fts_similarity_context_marks_shared():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE passages USING fts5(file UNINDEXED, tree UNINDEXED,"
        " ref UNINDEXED, title, author, text, leaf UNINDEXED)")
    conn.executemany(
        "INSERT INTO passages(file,tree,ref,title,author,text,leaf)"
        " VALUES (?,?,?,?,?,?,?)", [
            ("f", "default", "1", "T", "A", "περὶ τῆς αὐθαδείας λόγος", "1"),
            ("f", "default", "2", "T", "A", "ἄλλο τι περὶ αὐθαδείας", "1"),
        ])
    ctx = browse.fts_similarity_context(
        conn, ("f", "default", "1"), [("f", "default", "2")])
    c = ctx[("f", "default", "2")]
    assert "<mark>" in c["snip"]                       # shared words highlighted
    assert any("αὐθαδ" in w for w in c["shared"])      # and listed as chips
    # short function words (τι, < 4 chars) are not treated as shared
    assert "τι" not in c["shared"]


def test_fts_query_string_wildcard_and_terms():
    f = browse.fts_query_string
    assert f("λογ*") == '"λογ"*'              # trailing * -> prefix
    assert f("a b") == '"a" AND "b"'          # multi-term AND
    assert f("Λόγος") == '"Λόγος"'            # plain quoted term
    assert f("  ") == ""                       # empty -> empty (no MATCH)


class TestBetacode:
    """Beta Code -> Unicode Greek conversion (cllg_viewer.betacode.to_greek)."""

    def test_lowercase_with_accents(self):
        assert browse.to_greek("lo/gos") == "λόγος"
        assert browse.to_greek("mh=nin") == "μῆνιν"

    def test_breathings_and_uppercase(self):
        assert browse.to_greek("a)/nqrwpos") == "ἄνθρωπος"
        assert browse.to_greek("*)/anqrwpos") == "Ἄνθρωπος"  # diacritics after '*'
        assert browse.to_greek("r(o/dos") == "ῥόδος"

    def test_final_sigma(self):
        assert browse.to_greek("qeo/s") == "θεός"           # word-final -> ς
        assert browse.to_greek("ko/smos") == "κόσμος"

    def test_iota_subscript_and_diaeresis(self):
        assert browse.to_greek("a|") == "ᾳ"
        assert browse.to_greek("i+") == "ϊ"

    def test_non_letters_pass_through(self):
        # Spaces are preserved (word boundaries survive), so multi-word search works.
        assert browse.to_greek("lo/gos kai\\ qeo/s") == "λόγος καὶ θεός"

    def test_idempotent_on_greek(self):
        # Already-Greek text is untouched, so converting twice is safe (the server
        # converts even when the JS field already produced Greek).
        assert browse.to_greek("λόγος") == "λόγος"
        assert browse.to_greek(browse.to_greek("lo/gos")) == "λόγος"


class TestDtsLink:
    def test_identifier_from_metadata(self):
        from pathlib import Path
        path = Path(DATA, SAMPLE).resolve()
        assert browse.dts_identifier(path) == "urn:cts:greekLit:tlg1326.tlg001.cllg-grc1"

    def test_document_url(self):
        url = browse.dts_document_url("http://dts.test", "urn:x", "4", "default")
        assert url.startswith("http://dts.test/document/?")
        assert "resource=urn%3Ax" in url and "ref=4" in url

    def test_passage_page_shows_dts_link(self):
        from pathlib import Path
        app = browse.create_app(Path(DATA).resolve(), dts_base="http://dts.test")
        app.testing = True
        r = app.test_client().get("/passage?file=" + urllib.parse.quote(SAMPLE) + "&ref=4")
        body = r.get_data(as_text=True)
        assert "DTS API" in body
        assert "urn:cts:greekLit:tlg1326.tlg001.cllg-grc1" in body
