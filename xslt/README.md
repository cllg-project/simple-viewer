# Presentation layer: TEI → HTML / text + a corpus browser

This directory holds the stylesheets that turn the corpus' TEI into something a
human (HTML) or a search engine (plain text) can consume. The Flask app that
drives them lives in the `cllg_viewer/` package at the repo root (entry points
`browse.py` / `wsgi.py`).

## Why it is built this way

The corpus is served as **excerpts**: `dapytains` resolves a citation reference
to a *passage* — only part of the document tree (`Document.get_passage(ref)`
returns a reconstructed `<TEI><text><body>…just that unit…</body></text></TEI>`,
with no `teiHeader`). The stylesheets therefore **never assume they see a whole
document**. They render whatever subtree they are handed — a full `<TEI>`, a
reconstructed passage, or even a bare `<div>`/`<l>` — and a low-priority
catch-all template (`match="*"`) renders any element they do not explicitly
know about, so nothing in the ~180 distinct elements across the two corpora is
ever silently dropped.

## Files

| File | Purpose |
|------|---------|
| `tei-to-html.xsl` | Presentation transform → **HTML fragment** (no `<html>`/`<body>` wrapper). |
| `tei-to-text.xsl` | Same model, **plain UTF-8 text** for a search index. Drops notes & apparatus. |
| `tei.css` | Styles for the HTML; every rule documents the TEI source of its class. |
| `../cllg_viewer/rendering.py` | `Renderer`: compiles & runs both stylesheets (saxonche). |
| `../cllg_viewer/web.py` | Flask app; templates in `../templates`, chrome CSS in `../static`. |
| `../cllg_viewer/search.py` | `index` builder + FTS5 search over the plain-text output. |

Both stylesheets are XSLT 3.0 and run under the `saxonche` processor in the
project venv (`.venv/bin/python`). They use
`xpath-default-namespace="http://www.tei-c.org/ns/1.0"` so the TEI elements match
without a prefix.

## HTML output

The output is a flow of block and inline elements wrapped by the host page in a
`<div class="tei-text">`. Structure:

- `<div>` → `<section class="tei-div" data-type=… data-subtype=… data-n=…>`. A
  citeable div with no `<head>` gets a small visible reference tag
  `<span class="tei-ref-n">`.
- `<head>` → `<h2>`…`<h6>` (level follows div nesting), class `tei-head`.
- `<p>`/`<ab>` → `<p class="tei-p">` / `tei-ab`.
- Verse: `<lg>` → `<div class="tei-lg">`; `<l>` → `<div class="tei-l">` with a
  line-number gutter `<span class="tei-lineno">` and the text in
  `<span class="tei-l-text">` (whitespace preserved).
- `<hi>` is mapped to **semantic HTML** by `@rend`: `italic`→`<em>`, `bold`→
  `<strong>`, `sup`→`<sup>`, `sub`→`<sub>`; any other rend becomes
  `<span class="tei-hi tei-rend-<value>">`.
- `<choice>` shows the **reading text** (`corr` > `expan` > `reg`) and keeps the
  source variant (`sic`/`abbr`/`orig`) in the `title` attribute (hover to see).
- `<note>` is kept but folded by CSS to a hoverable `*` marker.
- `<gap>`→`[…]`, `<supplied>`→`⟨…⟩`, milestones (`pb`/`cb`/`lb`/`milestone`)
  become small inline markers.

### Custom classes

`tei.css` carries a full class-index comment at the top; each class names the TEI
element/attribute it comes from (`.tei-div`←`<div>`, `.tei-lineno`←`@n` of `<l>`,
`.tei-choice`←`<choice>`, `.tei`←catch-all for unnamed elements, etc.). The CSS
is purely presentational — the HTML is meaningful without it.

## Plain text (search) output

`tei-to-text.xsl` emits `method="text"`. It keeps reading text only:

- **drops** `<note>`, `<figure>`/`<graphic>`, `<teiHeader>`, and the non-reading
  side of `<choice>` (`sic`/`abbr`/`orig`);
- collapses milestones to a single space;
- ends each block (`div`/`p`/`ab`/`l`/`lg`/`head`/`item`) with a newline and
  normalises whitespace, while **preserving inter-word spacing** so adjacent
  inline words never merge into one search token.

## Running the browser

From the repository root:

```bash
.venv/bin/python browse.py serve              # http://127.0.0.1:5000  (browse corpus/data)
.venv/bin/python browse.py serve --root /path/to/data --port 5001
```

- `/` — home page, **organised Author → Work** (each author is a collapsible
  `<details>`), with a **search box** at the top.
- `/doc?file=…` shows the citation tree (`Document.get_reffs`).
- `/passage?file=…&ref=…` renders that excerpt as HTML; add `&format=text` for the
  plain-text view.

### Search

The home page has **two distinct search fields**, chosen with the radio above the
box:

- **Works** (default) — navigation: filters the Author → Work list by author or
  work name (substring match). Always on, needs no index.
- **Inside texts** — full-text content search (see below). Enabled only when an
  index is built and the server runs with `--fulltext`; otherwise the radio is
  disabled with a hint to run `make search`.

A **βῆτα code** checkbox lets you type polytonic Greek as ASCII
(`lo/gos` → λόγος, `*)/anqrwpos` → Ἄνθρωπος): the field transliterates live as you
type and a **hint bar** shows the diacritic legend (`)` smooth, `(` rough, `/`
acute, `\` grave, `=` circumflex, `|` iota, `+` diaeresis, `*` capital) plus a live
preview of the converted query. The conversion (`cllg_viewer/betacode.py`, mirrored
in `static/betacode.js`) builds each letter as a base character + Unicode combining
diacritics then NFC-normalises, and also runs server-side so direct URLs / no-JS
work. This matters because Greek search is accent-sensitive — Beta Code is how you
type the accents.

**Optional full-text search** over passage *content* is opt-in. Build the index
once (below), then start the server with `--fulltext`:

```bash
.venv/bin/python browse.py index            # build the index once
.venv/bin/python browse.py serve --fulltext # then content matches appear too
```

The engine is **SQLite FTS5** — part of the Python standard library, so there is
no external search service to run. Greek search is accent-sensitive (type the
accents); the Latin corpus folds diacritics. Without `--fulltext` the search stays
purely author/work navigation.

### DTS API link

Every reading page shows the passage's **DTS resource URN** (read from the work's
`metadata.xml`, e.g. `urn:cts:greekLit:tlg1326.tlg001.cllg-grc1`) and a **DTS API**
link to the corresponding Document endpoint
(`<dts-base>/document/?resource=<urn>&ref=<ref>`). Point it at a running
dapytains/DTS server with `--dts-base` (default `http://localhost:8000`).

## Building the (optional) search index

Only needed if you want the opt-in full-text search:

```bash
make index                         # all CPU cores -> var/search.sqlite
make index JOBS=4                  # fixed worker count
# or directly:
.venv/bin/python browse.py index --root corpus/data --db var/search.sqlite -j 4
.venv/bin/python browse.py index --root corpus/data --jsonl var/passages.jsonl
```

Indexing renders every passage with Saxon, which is CPU-bound and independent per
document, so it fans the documents out across a **process pool** — one Saxon
processor per worker (`-j/--jobs`, default = all CPU cores). Builds the FTS5
database from the note-free text of `tei-to-text.xsl`; `--jsonl` additionally dumps
one record per passage `{file, tree, ref, title, author, text}` — that JSONL is also
the plain-text export an external search engine would index. Note it indexes
**every citeable level** (e.g. both a fragment and each of its lines), so a
fragment's text also appears split across its line records.

Indexing is resilient: a document dapytains cannot navigate (e.g. a citeStructure
whose unit name compiles to an invalid regex group) is skipped, and a worker that
*wedges* on a pathological document (a native regex/Saxon spin that would
otherwise hang the whole run) is abandoned after `--timeout` seconds (default
120) without any result. Either way the run still finishes and the offending
files are written to `<db>.skipped.tsv`.

## Tests

```bash
.venv/bin/python -m pytest tests/test_presentation.py -q
```

Covers both stylesheets (including bare-subtree robustness and note-dropping) and
the Flask routes.
