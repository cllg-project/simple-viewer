// Keyboard navigation for full-text results: ↑/↓ move the active hit, rolling
// over to the previous/next page at the edges; ↵ opens the active hit.
(function () {
  "use strict";
  var box = document.getElementById("hits");
  if (!box) return;
  var hits = Array.prototype.slice.call(box.querySelectorAll(".hit"));
  if (!hits.length) return;

  var foot = document.querySelector(".result-foot");
  var page = foot ? parseInt(foot.dataset.page, 10) : 1;
  var pages = foot ? parseInt(foot.dataset.pages, 10) : 1;
  var base = foot ? foot.dataset.base : "";

  // Land on the first or last hit depending on which edge we rolled in from.
  var active = window.location.hash === "#last" ? hits.length - 1 : 0;

  function paint() {
    hits.forEach(function (h, i) { h.classList.toggle("active", i === active); });
    var el = hits[active];
    if (el) {
      var r = el.getBoundingClientRect();
      if (r.top < 70 || r.bottom > window.innerHeight) {
        window.scrollTo({ top: r.top + window.scrollY - window.innerHeight / 2,
                          behavior: "smooth" });
      }
    }
  }
  paint();

  function go(pageNo, edge) {
    window.location.href = base + "&page=" + pageNo + edge;
  }

  hits.forEach(function (h, i) {
    h.addEventListener("mouseenter", function () { active = i; paint(); });
  });

  window.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (active < hits.length - 1) { active++; paint(); }
      else if (page < pages) { go(page + 1, "#first"); }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (active > 0) { active--; paint(); }
      else if (page > 1) { go(page - 1, "#last"); }
    } else if (e.key === "Enter") {
      if (hits[active]) { window.location.href = hits[active].href; }
    }
  });
})();
