/*
 * app.js — progressive enhancement for the CLLG Light chrome.
 * No build step, no dependencies. Everything degrades gracefully without JS:
 * the catalogue, reader and search all work server-rendered; this only adds
 * the theme toggle, keyboard navigation, the rail filter and note popovers.
 */
(function () {
  "use strict";

  /* ---- theme toggle: cycle auto → light → dark, persisted in localStorage -- */
  var ICONS = {
    sun: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M2 12h2.5M19.5 12H22M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg>',
    moon: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 13.2A8.5 8.5 0 1 1 10.8 3a6.6 6.6 0 0 0 10.2 10.2z"/></svg>',
    auto: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>'
  };
  function readTheme() {
    try { return localStorage.getItem("cllg-theme") || "auto"; } catch (e) { return "auto"; }
  }
  function applyTheme(t) {
    var r = document.documentElement;
    if (t === "auto") r.removeAttribute("data-theme");
    else r.setAttribute("data-theme", t);
    try { localStorage.setItem("cllg-theme", t); } catch (e) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.innerHTML = ICONS[t === "dark" ? "moon" : t === "light" ? "sun" : "auto"];
      btn.title = "Theme: " + t;
    }
  }
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    applyTheme(readTheme());
    toggle.addEventListener("click", function () {
      var order = ["auto", "light", "dark"];
      applyTheme(order[(order.indexOf(readTheme()) + 1) % 3]);
    });
  }

  /* ---- home: alphabet jump rail ------------------------------------------- */
  // The header and the search bar are both sticky near the top, so the letter
  // dividers (and a #alpha-X jump) land *under* them — it looks like nothing
  // moved. Publish the real chrome height as --cat-sticky so the dividers pin
  // just below it (CSS), and use it to offset the jump. Recomputed on resize and
  // when the search bar grows/shrinks (e.g. the β-code hint bar toggling).
  var alphaRail = document.querySelector(".alpha-rail");
  if (alphaRail) {
    var header = document.querySelector(".site-header");
    var search = document.querySelector(".search");
    function chromeHeight() {
      return (header ? header.offsetHeight : 56) + (search ? search.offsetHeight : 0);
    }
    function publishStickyTop() {
      document.documentElement.style.setProperty("--cat-sticky", chromeHeight() + "px");
    }
    publishStickyTop();
    window.addEventListener("resize", publishStickyTop);
    if (window.ResizeObserver && search) new ResizeObserver(publishStickyTop).observe(search);

    // Absolute document top via the offset chain — unlike getBoundingClientRect this
    // is NOT skewed by the divider's own sticky paint offset, and unlike
    // scrollIntoView() it actually scrolls *to* a sticky target (which Chromium
    // otherwise treats as already-in-place, so upward jumps did nothing).
    function docTop(el) {
      var y = 0;
      for (var n = el; n; n = n.offsetParent) y += n.offsetTop;
      return y;
    }

    alphaRail.addEventListener("click", function (e) {
      var link = e.target.closest('a[href^="#alpha-"]');
      if (!link) return;
      var target = document.getElementById(link.getAttribute("href").slice(1));
      if (!target) return;
      e.preventDefault();
      // Land the target's flow-top exactly at the sticky line so its own divider
      // pins at the top (the previous letter's divider then releases).
      var y = Math.max(0, docTop(target) - chromeHeight());
      window.scrollTo({ top: y, behavior: "smooth" });
      if (history.replaceState) history.replaceState(null, "", link.getAttribute("href"));
    });
  }

  /* ---- "/" focuses the most relevant search field ------------------------- */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    var el = document.activeElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
    var field = document.getElementById("rail-filter") || document.getElementById("search-input");
    if (field) { e.preventDefault(); field.focus(); }
  });

  /* ---- reader: ← / → move between passages -------------------------------- */
  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var el = document.activeElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
    var link = null;
    if (e.key === "ArrowLeft") link = document.querySelector('.pager a[rel="prev"]');
    else if (e.key === "ArrowRight") link = document.querySelector('.pager a[rel="next"]');
    if (link) { e.preventDefault(); window.location.href = link.href; }
  });

  /* ---- nav rail: filter references as you type ---------------------------- */
  var railFilter = document.getElementById("rail-filter");
  var reffs = document.getElementById("reffs");
  if (railFilter && reffs) {
    var rows = Array.prototype.slice.call(reffs.querySelectorAll("a"));
    var empty = reffs.querySelector(".reff-group-empty");
    railFilter.addEventListener("input", function () {
      var q = railFilter.value.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (a) {
        var hit = !q || (a.getAttribute("data-text") || "").indexOf(q) !== -1;
        a.style.display = hit ? "" : "none";
        if (hit) shown++;
      });
      if (empty) empty.style.display = shown ? "none" : "";
      else if (!shown && q) {
        empty = document.createElement("div");
        empty.className = "reff-group-empty";
        empty.textContent = "No matching reference.";
        reffs.appendChild(empty);
      }
    });
  }

  /* ---- nav rail: scroll the current passage into view --------------------- */
  if (reffs) {
    var cur = reffs.querySelector("a.is-current");
    if (cur) reffs.scrollTop = cur.offsetTop - reffs.clientHeight / 2 + cur.clientHeight / 2;
  }

  /* ---- mobile drawer for the nav rail ------------------------------------- */
  var railToggle = document.getElementById("rail-toggle");
  var scrim = document.getElementById("rail-scrim");
  var reader = document.querySelector(".reader");
  function setRail(open) { if (reader) reader.classList.toggle("rail-open", open); }
  if (railToggle) railToggle.addEventListener("click", function () {
    if (reader) setRail(!reader.classList.contains("rail-open"));
  });
  if (scrim) scrim.addEventListener("click", function () { setRail(false); });

  /* ---- TEI notes: click the marker to pin the popover open ---------------- */
  document.addEventListener("click", function (e) {
    var note = e.target.closest ? e.target.closest(".tei-note") : null;
    document.querySelectorAll(".tei-note.open").forEach(function (n) {
      if (n !== note) n.classList.remove("open");
    });
    if (note) { e.preventDefault(); note.classList.toggle("open"); }
  });
})();
