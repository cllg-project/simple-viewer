// "Similar passages" panel: on-demand semantic neighbours for the open passage.
// States on the <section id="similar">: is-idle | is-loading | is-open.
(function () {
  "use strict";
  var sec = document.getElementById("similar");
  if (!sec) return;

  var runBtn = document.getElementById("similar-run");
  var hideBtn = document.getElementById("similar-hide");
  var list = document.getElementById("similar-list");
  var foot = document.getElementById("similar-foot");
  var countEl = document.getElementById("similar-count");
  var linkBtn = document.getElementById("similar-link");      // toolbar jump link
  var linkN = document.getElementById("similar-link-n");
  var state = "idle";
  var loaded = false;

  var SVG_RIGHT = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';

  function setState(s) {
    state = s;
    sec.className = "similar is-" + s;
  }

  function band(score) {
    if (score >= 0.9) return "strong";
    if (score >= 0.75) return "high";
    if (score >= 0.6) return "mid";
    return "low";
  }

  function el(tag, cls, attrs) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  function skeleton() {
    list.innerHTML = "";
    for (var i = 0; i < 3; i++) {
      var li = el("li");
      var row = el("div", "sim-row sim-skel");
      row.appendChild(el("div", "sim-gauge"));
      var body = el("div", "sim-body");
      body.appendChild(el("div", "skel-line w40"));
      body.appendChild(el("div", "skel-line w90"));
      row.appendChild(body);
      li.appendChild(row);
      list.appendChild(li);
    }
  }

  function passageHref(h) {
    var id = h.urn ? ("urn=" + encodeURIComponent(h.urn))
                   : ("file=" + encodeURIComponent(h.file));
    return "/passage?" + id + "&ref=" + encodeURIComponent(h.ref) +
           "&tree=" + encodeURIComponent(h.tree);
  }

  function renderRow(h, i) {
    var pct = Math.round((h.score || 0) * 100);
    var li = el("li");
    var a = el("a", "sim-row band-" + band(h.score || 0), { href: passageHref(h) });

    var gauge = el("div", "sim-gauge", { role: "img", "aria-label": pct + "% match" });
    var rank = el("span", "sim-rank"); rank.textContent = (i + 1);
    var pctEl = el("span", "sim-pct"); pctEl.textContent = pct;
    var pi = document.createElement("i"); pi.textContent = "%"; pctEl.appendChild(pi);
    var track = el("span", "sim-track");
    var fill = el("span", "sim-fill"); fill.style.width = pct + "%";
    track.appendChild(fill);
    gauge.appendChild(rank); gauge.appendChild(pctEl); gauge.appendChild(track);
    a.appendChild(gauge);

    var body = el("div", "sim-body");
    var meta = el("div", "sim-meta");
    var au = el("span", "sim-author"); au.textContent = h.author || "";
    var work = el("span", "sim-work", { lang: "la", title: h.title || h.file });
    work.textContent = h.title || h.file;
    var ref = el("span", "sim-ref"); ref.textContent = "§ " + h.ref;
    meta.appendChild(au); meta.appendChild(work); meta.appendChild(ref);
    body.appendChild(meta);

    var snip = el("div", "sim-snip", { lang: "grc" });
    if (h.snip) { snip.innerHTML = h.snip; }      // server-escaped, marks only
    else { snip.textContent = h.excerpt || ""; }
    body.appendChild(snip);

    if (h.shared && h.shared.length) {
      var shared = el("div", "sim-shared");
      var lbl = el("span", "sim-shared-lbl"); lbl.textContent = "shared";
      shared.appendChild(lbl);
      h.shared.forEach(function (w) {
        var chip = el("span", "sim-chip", { lang: "grc" });
        chip.textContent = w; shared.appendChild(chip);
      });
      body.appendChild(shared);
    }
    a.appendChild(body);

    var go = el("span", "sim-go", { "aria-hidden": "true" });
    go.innerHTML = SVG_RIGHT;
    a.appendChild(go);

    li.appendChild(a);
    return li;
  }

  function renderEmpty() {
    list.innerHTML = "";
    var li = el("li");
    var p = el("div", "sim-row");
    p.style.color = "var(--ink-3)";
    p.textContent = "No similar passages found.";
    li.appendChild(p);
    list.appendChild(li);
  }

  function setCount(n) {
    countEl.querySelector("b").textContent = n;
    countEl.hidden = false;
    if (linkN) { linkN.textContent = n; linkN.hidden = false; }
  }

  function fetchSimilar() {
    if (loaded || state === "loading") return Promise.resolve();
    setState("loading");
    skeleton();
    var params = new URLSearchParams({ ref: sec.dataset.ref, tree: sec.dataset.tree });
    if (sec.dataset.urn) { params.set("urn", sec.dataset.urn); }
    else { params.set("file", sec.dataset.file); }
    return fetch("/similar?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var hits = (data && data.hits) || [];
        list.innerHTML = "";
        if (!hits.length) { renderEmpty(); }
        else { hits.forEach(function (h, i) { list.appendChild(renderRow(h, i)); }); }
        setCount(hits.length);
        runBtn.hidden = true;
        foot.hidden = false;
        loaded = true;
        setState("open");
      })
      .catch(function () {
        list.innerHTML = "";
        renderEmpty();
        setState("open");
      });
  }

  function scrollToPanel() {
    var y = sec.getBoundingClientRect().top + window.scrollY - 70;
    window.scrollTo({ top: y, behavior: "smooth" });
  }

  runBtn.addEventListener("click", fetchSimilar);
  if (linkBtn) {
    linkBtn.addEventListener("click", function () {
      fetchSimilar();
      window.requestAnimationFrame(scrollToPanel);
    });
  }
  hideBtn.addEventListener("click", function () {
    setState("idle");
    list.innerHTML = "";
    foot.hidden = true;
    countEl.hidden = true;
    runBtn.hidden = false;
    loaded = false;
  });
})();
