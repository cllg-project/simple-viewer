"""
Beta Code -> Unicode Greek.

Beta Code lets you type polytonic Greek with ASCII: `lo/gos` -> λόγος,
`*)/anqrwpos` -> Ἄνθρωπος.  Rather than carry a huge precomposed lookup table we
build each letter as a base character + Unicode *combining* diacritics and then
NFC-normalise, which composes them into the precomposed forms the corpus (and the
FTS index) actually store.  The same algorithm is mirrored in static/betacode.js
for the live in-field conversion, so server and browser agree.

Greek search is accent-sensitive (the FTS tokenizer folds Latin diacritics, not
Greek), which is exactly why typing the accents via Beta Code is useful.
"""
from __future__ import annotations

import re
import unicodedata

# Beta Code letter -> Greek base letter (lowercase; uppercased on '*' or ASCII caps).
_LETTERS = {
    "a": "α", "b": "β", "g": "γ", "d": "δ", "e": "ε", "z": "ζ", "h": "η",
    "q": "θ", "i": "ι", "k": "κ", "l": "λ", "m": "μ", "n": "ν", "c": "ξ",
    "o": "ο", "p": "π", "r": "ρ", "s": "σ", "t": "τ", "u": "υ", "f": "φ",
    "x": "χ", "y": "ψ", "w": "ω", "v": "ϝ",
}

# Beta Code diacritic -> combining mark (attached to the preceding letter).
_DIACRITICS = {
    ")": "̓",   # smooth breathing (psili)
    "(": "̔",   # rough breathing (dasia)
    "/": "́",   # acute (oxia)
    "\\": "̀",  # grave (varia)
    "=": "͂",   # circumflex (perispomeni)
    "+": "̈",   # diaeresis
    "|": "ͅ",   # iota subscript (ypogegrammeni)
}

_GREEK = "Ͱ-Ͽἀ-῿"  # ranges used for the final-sigma test


def to_greek(text: str) -> str:
    """Convert a Beta Code string to NFC Greek.

    Diacritics attach to the preceding letter — including a letter that was
    *already* Greek — so incremental input (`λο` + `/` -> `λό`) works, which the
    live JS converter relies on.  Non-Beta characters pass through unchanged, so
    Latin queries are left alone.
    """
    clusters: list[list] = []  # each: [base_char, [combining_marks]]
    pending_upper = False       # saw '*'; the next letter is uppercase
    pending_marks: list[str] = []  # diacritics typed between '*' and its letter
    for ch in text:
        if ch == "*":            # next letter is uppercase
            pending_upper = True
            pending_marks = []
            continue
        if ch in _DIACRITICS:
            mark = _DIACRITICS[ch]
            if pending_upper:    # belongs to the upcoming uppercase letter (*)/a)
                pending_marks.append(mark)
            elif clusters:       # attach to the current (possibly already-Greek) letter
                clusters[-1][1].append(mark)
            continue
        low = ch.lower()
        if low in _LETTERS:
            base = _LETTERS[low]
            if pending_upper or ch.isupper():
                base = base.upper()
            clusters.append([base, list(pending_marks)])
        else:                    # already-Greek letter or any other char: passthrough
            clusters.append([ch, list(pending_marks)])
        pending_upper = False
        pending_marks = []

    s = "".join(base + "".join(marks) for base, marks in clusters)
    # A σ that is not followed by another Greek letter is word-final -> ς.
    s = re.sub(rf"σ(?![{_GREEK}])", "ς", s)
    return unicodedata.normalize("NFC", s)
