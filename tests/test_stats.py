"""Tests for the whole-work token statistics view (cllg_viewer.stats + /stats)."""
import os
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cllg_viewer as browse  # noqa: E402
from cllg_viewer.stats import compute_work_stats  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "fixtures", "corpus", "data")
SAMPLE = "tlg1326/tlg001/tlg1326.tlg001.cllg-grc1.xml"


class TestComputeStats:
    def test_counts_and_folding(self):
        # καὶ/καί fold (grave→acute); Λόγος/λόγος fold (case + final sigma).
        text = "καὶ ὁ λόγος καί ἡ ἀρετή· λόγος δὲ καὶ Λόγος. ἀρετή ἀρετή σοφία"
        s = compute_work_stats(text, lang="grc", top_n=6)
        assert s["tokens"] == 13
        forms = {w["form"]: w for w in s["top"]}
        assert forms["καί"]["count"] == 3          # 3 spellings merged
        assert forms["λόγος"]["count"] == 3        # case + final-sigma merged
        assert s["types"] == 7
        assert s["hapax"] == 4                      # δέ, ἡ, σοφία ... occur once
        assert abs(s["ttr"] - 7 / 13) < 1e-9

    def test_content_vs_function_split(self):
        s = compute_work_stats("καὶ ὁ λόγος δὲ ἀρετή", lang="grc")
        by = {w["form"]: w for w in s["top"]}
        assert by["καί"]["content"] is False and by["καί"]["pos"] == "conj"
        assert by["ὁ"]["content"] is False
        assert by["λόγος"]["content"] is True and by["λόγος"]["pos"] == "lexical"
        # composition is by running tokens and sums to the total
        assert s["composition"]["fn"] + s["composition"]["content"] == s["tokens"]
        assert s["first_content"]["form"] == "λόγος"

    def test_latin_stoplist(self):
        s = compute_work_stats("et in ad amor virtus amor", lang="la")
        by = {w["form"]: w for w in s["top"]}
        assert by["et"]["content"] is False and by["et"]["pos"] == "conj"
        assert by["amor"]["content"] is True and by["amor"]["count"] == 2

    def test_zipf_and_coverage(self):
        s = compute_work_stats(" ".join(["α"] * 5 + ["β"] * 3 + ["γ"] * 1), lang="grc")
        assert [z["freq"] for z in s["zipf"]] == [5, 3, 1]   # sorted desc
        assert s["zipf"][0]["rank"] == 1
        assert abs(s["top_coverage"] - 1.0) < 1e-9            # top covers all here

    def test_empty_text(self):
        s = compute_work_stats("", lang="grc")
        assert s["tokens"] == 0 and s["top"] == [] and s["zipf_geom"] is None


class TestStatsRoute:
    @pytest.fixture(scope="class")
    def client(self):
        app = browse.create_app(Path(DATA).resolve())
        app.testing = True
        return app.test_client()

    def test_page_renders(self, client):
        r = client.get("/stats?file=" + urllib.parse.quote(SAMPLE))
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert 'class="stat-cards"' in body
        assert "Most frequent forms" in body
        assert 'class="zipf-svg"' in body
        assert "Token statistics" in body
        # frequent-form rows link into inside-texts search
        assert "/?scope=text&q=" in body

    def test_back_returns_to_passage(self, client):
        # when ?ref= is given, "Back to reading" targets that passage
        r = client.get("/stats?file=" + urllib.parse.quote(SAMPLE) + "&ref=4")
        body = r.get_data(as_text=True)
        assert 'class="btn btn--ghost stats-back"' in body
        assert "ref=4" in body and "/passage?" in body

    def test_back_defaults_to_doc(self, client):
        # without a ref, it falls back to the work landing
        r = client.get("/stats?file=" + urllib.parse.quote(SAMPLE))
        body = r.get_data(as_text=True)
        import re
        m = re.search(r'stats-back" href="([^"]+)"', body)
        assert m and "/doc?" in m.group(1)

    def test_entry_points(self, client):
        doc = client.get("/doc?file=" + urllib.parse.quote(SAMPLE)).get_data(as_text=True)
        assert "Word statistics" in doc and "/stats?" in doc
        psg = client.get("/passage?file=" + urllib.parse.quote(SAMPLE)
                         + "&ref=4").get_data(as_text=True)
        assert ">statistics<" in psg and "/stats?" in psg

    def test_addressable_by_urn(self, client):
        from cllg_viewer.corpus import dts_identifier
        urn = dts_identifier(Path(DATA, SAMPLE))
        r = client.get("/stats?urn=" + urllib.parse.quote(urn))
        assert r.status_code == 200
        assert 'class="stat-cards"' in r.get_data(as_text=True)
