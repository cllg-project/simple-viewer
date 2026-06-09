"""
Whole-work token statistics for the "Token statistics" view.

Counts are computed over the *full* work's plain text (the same text-mode Saxon
render the FTS index uses), not a single passage.  There is no POS tagger in the
dependency set, so the function-vs-lexical split falls back to a curated
function-word stoplist per language (Greek / Latin); `content` is simply "not a
function word".  Surface forms are counted (lower-cased, with Greek orthographic
grave→acute and final-sigma folded so καὶ/καί and Λόγος/λόγος collapse).

`compute_work_stats` is pure (text in, dict out) so it is trivially cacheable per
work and the underlying text source can evolve without touching the view.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import List, Optional

DEFAULT_TOP_N = 25
DEFAULT_ZIPF_N = 120

# --------------------------------------------------------------------------- #
# Function-word stoplists (accent-stripped, lower-cased keys -> POS category).
# A heuristic fallback for the lexical/function split — not a lemmatiser.
# --------------------------------------------------------------------------- #
_GREEK_FUNCTION = {
    # articles
    **{w: "art" for w in (
        "ο", "η", "το", "τον", "την", "τους", "τας", "τα", "του", "της", "των",
        "τω", "τη", "τοις", "ταις", "οι", "αι")},
    # particles
    **{w: "part" for w in (
        "δε", "μεν", "γαρ", "τε", "δη", "αν", "αρα", "γε", "μην", "τοι", "ουν",
        "περ", "που")},
    # conjunctions
    **{w: "conj" for w in (
        "και", "αλλα", "η", "ει", "ως", "οτι", "ινα", "ωστε", "ουτε", "μητε",
        "ηδε", "ειτε", "εαν", "ουδε", "μηδε", "καθα", "επει", "οταν")},
    # prepositions
    **{w: "prep" for w in (
        "εν", "εις", "εκ", "εξ", "προς", "δια", "επι", "κατα", "μετα", "περι",
        "υπο", "απο", "ανα", "συν", "παρα", "υπερ", "προ", "αμφι", "αντι")},
    # pronouns
    **{w: "pron" for w in (
        "αυτος", "αυτου", "αυτον", "αυτη", "αυτω", "αυτων", "αυτην", "αυτοις",
        "ος", "ουτος", "ουτου", "οδε", "εκεινος", "τις", "τι", "εγω", "συ",
        "ημεις", "υμεις", "σφεις", "εαυτου", "ος", "ην", "οις")},
    # adverbs / negation
    **{w: "adv" for w in (
        "ου", "ουκ", "ουχ", "μη", "ναι", "νυν", "ετι", "ηδη", "αει", "ουτω",
        "ουτως", "μαλα", "λιαν")},
}

_LATIN_FUNCTION = {
    **{w: "conj" for w in (
        "et", "ac", "atque", "aut", "vel", "nec", "neque", "sed", "nam", "enim",
        "autem", "igitur", "ergo", "que", "ut", "cum", "si", "quod", "quia",
        "quoniam", "dum", "ne", "ve", "seu", "sive", "nisi", "tamen", "vero")},
    **{w: "prep" for w in (
        "in", "ad", "ex", "de", "per", "pro", "sub", "ab", "a", "ob", "inter",
        "ante", "post", "sine", "super", "propter", "apud", "contra", "trans",
        "e", "cum", "circa", "supra")},
    **{w: "pron" for w in (
        "qui", "quae", "quod", "is", "ea", "id", "hic", "haec", "hoc", "ille",
        "illa", "illud", "ipse", "ipsa", "se", "ego", "tu", "nos", "vos", "suus",
        "eius", "eum", "eam", "quem", "quam", "quibus", "cui", "sui", "sibi")},
    **{w: "adv" for w in (
        "non", "iam", "etiam", "quoque", "tam", "sic", "ita", "modo", "nunc",
        "tunc", "semper", "magis", "satis", "adhuc")},
}

_POS_LABELS = {
    "art": "article", "part": "particle", "conj": "conjunction",
    "prep": "preposition", "pron": "pronoun", "adv": "adverb",
    "lexical": "lexical word",
}


def _strip_accents(form: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", form)
                   if unicodedata.category(c) != "Mn")


def _normalize(form: str, lang: str) -> str:
    """Lower-case a surface form, folding Greek orthographic variants so that the
    same word collapses: grave→acute (καὶ→καί) and word-final Σ/σ→ς (Λόγος→λόγος)."""
    if lang == "grc":
        decomposed = unicodedata.normalize("NFD", form).replace("̀", "́")
        form = unicodedata.normalize("NFC", decomposed)
    form = form.lower()
    if lang == "grc" and form.endswith("σ"):     # medial σ at word end → ς
        form = form[:-1] + "ς"
    return form


def _pos_of(form: str, lang: str):
    """(pos, is_content) for a normalized form via the function-word stoplist."""
    table = _LATIN_FUNCTION if lang == "la" else _GREEK_FUNCTION
    pos = table.get(_strip_accents(form))
    return (pos, False) if pos else ("lexical", True)


def zipf_geometry(series: List[dict], top: List[dict]) -> Optional[dict]:
    """Pre-compute the log–log Zipf curve geometry (matches the design prototype)
    so the template stays declarative.  None when there is too little to plot."""
    if len(series) < 2 or series[0]["freq"] < 2:
        return None
    W, H, padL, padR, padT, padB = 560, 300, 46, 14, 16, 40
    iw, ih = W - padL - padR, H - padT - padB
    x_max = math.log10(series[-1]["rank"]) or 1.0
    y_max = math.log10(series[0]["freq"]) or 1.0

    def X(r): return padL + (math.log10(r) / x_max) * iw
    def Y(f): return padT + (1 - math.log10(f) / y_max) * ih

    path = " ".join(("L" if i else "M") + f"{X(p['rank']):.1f} {Y(p['freq']):.1f}"
                    for i, p in enumerate(series))
    max_rank, max_freq = series[-1]["rank"], series[0]["freq"]
    x_ticks = [{"x": round(X(t), 1), "label": t}
               for t in (1, 2, 5, 10, 20, 50, 100) if t <= max_rank]
    y_ticks = [{"y": round(Y(t), 1), "label": t}
               for t in (10, 30, 100, 300, 800) if 1 <= t <= max_freq]
    marks = [{"x": round(X(w["rank"]), 1), "y": round(Y(w["count"]), 1),
              "form": w["form"], "label": w["rank"] <= 4}
             for w in top[:6]]
    return {"w": W, "h": H, "padL": padL, "padR": padR, "padT": padT, "padB": padB,
            "iw": iw, "ih": ih, "path": path,
            "x_ticks": x_ticks, "y_ticks": y_ticks, "marks": marks}


def compute_work_stats(text: str, lang: str = "grc", top_n: int = DEFAULT_TOP_N,
                       zipf_n: int = DEFAULT_ZIPF_N) -> dict:
    """Token statistics over a whole work's plain text.  See module docstring."""
    text = unicodedata.normalize("NFC", text or "")
    counts: Counter = Counter()
    char_total = 0
    for raw in re.findall(r"\w+", text, flags=re.UNICODE):
        form = _normalize(raw, lang)
        if not form:
            continue
        counts[form] += 1
        char_total += len(form)

    tokens = sum(counts.values())
    base = {"tokens": tokens, "types": 0, "hapax": 0, "ttr": 0.0,
            "hapax_share": 0.0, "mean_word_len": 0.0, "top_coverage": 0.0,
            "top": [], "first_content": None, "zipf": [], "zipf_geom": None,
            "composition": {"fn": 0, "content": 0}, "lang": lang}
    if not tokens:
        return base

    types = len(counts)
    hapax = sum(1 for c in counts.values() if c == 1)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    top = []
    for i, (form, count) in enumerate(ordered[:top_n]):
        pos, content = _pos_of(form, lang)
        top.append({"rank": i + 1, "form": form, "count": count,
                    "share": count / tokens, "content": content, "pos": pos,
                    "pos_label": _POS_LABELS.get(pos, pos)})

    fn_tokens = sum(c for form, c in counts.items()
                    if not _pos_of(form, lang)[1])
    first_content = None
    for i, (form, count) in enumerate(ordered):
        if _pos_of(form, lang)[1]:
            first_content = {"form": form, "rank": i + 1, "count": count}
            break

    zipf = [{"rank": i + 1, "freq": c} for i, (form, c) in enumerate(ordered[:zipf_n])]

    base.update({
        "types": types, "hapax": hapax,
        "ttr": types / tokens, "hapax_share": hapax / types,
        "mean_word_len": char_total / tokens,
        "top_coverage": sum(w["count"] for w in top) / tokens,
        "top": top, "first_content": first_content, "zipf": zipf,
        "zipf_geom": zipf_geometry(zipf, top),
        "composition": {"fn": fn_tokens, "content": tokens - fn_tokens},
    })
    return base
