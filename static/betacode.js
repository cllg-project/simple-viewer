/*
 * Beta Code -> Unicode Greek, live in the search field.  Mirrors
 * cllg_viewer/betacode.py: build base letter + combining diacritics, then NFC.
 * The server converts too (for no-JS / direct URLs); this makes the field show
 * Greek as you type and drives the hint bar's live "raw → Greek" preview.
 *
 * Keep toGreek() in sync with cllg_viewer/betacode.py (the conversion is the
 * shared contract; the field UX below is browser-only).
 */
(function () {
  "use strict";

  var LETTERS = {
    a: "α", b: "β", g: "γ", d: "δ", e: "ε", z: "ζ", h: "η", q: "θ", i: "ι",
    k: "κ", l: "λ", m: "μ", n: "ν", c: "ξ", o: "ο", p: "π", r: "ρ", s: "σ",
    t: "τ", u: "υ", f: "φ", x: "χ", y: "ψ", w: "ω", v: "ϝ"
  };
  var DIA = {
    ")": "̓", "(": "̔", "/": "́", "\\": "̀",
    "=": "͂", "+": "̈", "|": "ͅ"
  };

  function toGreek(text) {
    var clusters = [];            // [baseChar, [marks]]
    var pendingUpper = false, pendingMarks = [];
    for (var ch of text) {
      if (ch === "*") { pendingUpper = true; pendingMarks = []; continue; }
      if (DIA[ch] !== undefined) {
        if (pendingUpper) pendingMarks.push(DIA[ch]);
        else if (clusters.length) clusters[clusters.length - 1][1].push(DIA[ch]);
        continue;
      }
      var low = ch.toLowerCase();
      var base;
      if (LETTERS[low] !== undefined) {
        base = LETTERS[low];
        if (pendingUpper || ch !== low) base = base.toUpperCase();
      } else {
        base = ch;              // already-Greek letter or any other char
      }
      clusters.push([base, pendingMarks.slice()]);
      pendingUpper = false; pendingMarks = [];
    }
    var s = clusters.map(function (c) { return c[0] + c[1].join(""); }).join("");
    s = s.replace(/σ(?![Ͱ-Ͽἀ-῿])/g, "ς");  // final sigma
    return s.normalize("NFC");
  }
  window.betacode = toGreek;

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("search-input");
    var toggle = document.getElementById("beta-toggle");
    var bar = document.getElementById("hint-bar");
    var fromEl = document.getElementById("beta-from");
    var toEl = document.getElementById("beta-to");
    if (!input || !toggle) return;

    // The field's value is the *raw* beta code the user typed; we keep it in a
    // shadow buffer and show the converted Greek, so the conversion is always
    // reversible for further editing.
    var raw = input.value;

    function refreshPreview() {
      if (!fromEl || !toEl) return;
      var tokens = raw.trim().split(/\s+/);
      var last = tokens[tokens.length - 1] || "lo/gos";
      fromEl.textContent = last;
      toEl.textContent = toGreek(last);
    }

    function render() {
      var greek = toGreek(raw);
      if (input.value !== greek) input.value = greek;
      refreshPreview();
    }

    function setEnabled(on) {
      if (bar) bar.hidden = !on;
      if (on) { raw = input.value && !/[Ͱ-Ͽἀ-῿]/.test(input.value) ? input.value : raw; render(); }
    }

    // While beta code is on we intercept key input so we can track the raw ASCII.
    input.addEventListener("keydown", function (e) {
      if (!toggle.checked) return;
      if (e.key === "Enter") return;                 // let the form submit
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Backspace") { e.preventDefault(); raw = raw.slice(0, -1); render(); }
      else if (e.key.length === 1) { e.preventDefault(); raw += e.key; render(); }
    });
    // Plain typing (beta off) keeps the field as a normal search input.
    input.addEventListener("input", function () { if (!toggle.checked) raw = input.value; });

    toggle.addEventListener("change", function () { setEnabled(toggle.checked); });
    setEnabled(toggle.checked);   // honour the server-rendered initial state
  });
})();
