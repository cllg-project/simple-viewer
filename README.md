# CLLG simple viewer

A small, self-contained web app to **browse the [CLLG freed-corpus][corpus]** —
TEI editions of (mostly fragmentary) Greek and Latin texts — as readable HTML.

It resolves a citation reference to a passage (an *excerpt*) with
[dapytains][dapytains], renders it to HTML with an XSLT 3.0 presentation
stylesheet (run by [SaxonC][saxonche]), and serves it with Flask. The home page is
organised **Author → Work** with a search box to find your way around.

![what it does](https://img.shields.io/badge/TEI-%E2%86%92%20HTML-blue)

## Features

- **Author → Work navigation** with an always-on search box over author/work names.
- **Excerpt rendering**: any citeable passage (`dapytains` extracts only that part
  of the document tree) is rendered to clean, styled HTML. The stylesheet is robust
  to receiving a partial subtree.
- **Prev / next navigation** on every reading page, flowing continuously in
  document order at the current citation level (last line of a fragment → first
  line of the next).
- **Cached catalog**: the Author → Work index is scanned once per worker and cached
  (≈60× faster on repeat loads); restart the server to pick up corpus changes.
- **Plain-text view** of any passage (notes stripped) — the same transform a search
  engine would index.
- **Optional full-text search** (opt-in): a self-contained SQLite FTS5 index, no
  external service.
- **DTS API link** on every reading page (the passage's CTS URN + a deep link into a
  running DTS server).

## Quick start

```bash
make install     # create the venv, install deps, clone the corpus texts
make run         # serve with gunicorn at http://127.0.0.1:8000
# or, for development:
make dev         # Flask dev server (auto-uses the cloned corpus)
```

`make install` does three things: creates `.venv`, installs `requirements.txt`, and
shallow-clones the corpus repository into `./corpus`. Override the source or
location:

```bash
make install CORPUS_REPO=https://example.org/your/corpus.git CORPUS_DIR=corpus
```

## Running

| Command | What it does |
|---------|--------------|
| `make run`  | Production server via **gunicorn** (`wsgi:app`), `HOST`/`PORT`/`WORKERS` overridable. |
| `make dev`  | Flask development server. |
| `make index`| Build the optional full-text search index (`var/search.sqlite`). |
| `make test` | Run the test suite (uses an embedded fixture corpus — no clone needed). |
| `make clean`| Remove the venv, the index and the cloned corpus. |

Examples:

```bash
make run PORT=9000 WORKERS=4
make index && make run                 # build index, then...
CLLG_FULLTEXT=1 make run               # ...serve with full-text search enabled
```

### Configuration (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `CLLG_ROOT` | `./corpus/data` | corpus data directory (CapiTainS layout) |
| `CLLG_DB` | `./var/search.sqlite` | full-text index path |
| `CLLG_DTS_BASE` | `http://localhost:8000` | DTS server base URL for the reading-page link |
| `CLLG_FULLTEXT` | *(off)* | set to `1`/`true` to enable full-text search under gunicorn |

The CLI flags `--root`, `--db`, `--dts-base`, `--fulltext` override these.

## How it works

```
 corpus/data/**/<author>.<work>.<edition>-<lang>1.xml   (TEI editions)
        │
        ▼  dapytains.Document.get_passage(ref)        ← extract one excerpt
   lxml subtree
        │
        ▼  saxonche + xslt/tei-to-html.xsl            ← present it
   HTML fragment  ──►  Flask  ──►  browser
```

- `cllg_viewer/` — the package: `rendering` (Saxon), `corpus` (discovery/DTS),
  `search` (FTS5 + parallel index), `web` (Flask app), `cli` (argparse).
- `browse.py` / `wsgi.py` — thin entry points (`serve` / `index` CLI; `gunicorn wsgi:app`).
- `templates/` + `static/app.css` — the UI chrome (Jinja templates + page styles).
- `xslt/tei-to-html.xsl` — TEI → HTML presentation transform.
- `xslt/tei-to-text.xsl` — TEI → plain text (notes dropped) for indexing.
- `xslt/tei.css` — styles; every class documents its TEI source.
- `xslt/README.md` — detailed description of the HTML output and CSS classes.

See **[xslt/README.md](xslt/README.md)** for the full description of the HTML
output, the class table, and the search/DTS behaviour.

## Routes

| Route | Purpose |
|-------|---------|
| `/` | home — Author → Work, with the navigation search box |
| `/doc?file=…` | citation tree for one work |
| `/passage?file=…&ref=…` | a passage as HTML (`&format=text` for plain text) |
| `/tei.css` | the stylesheet |

## Tests / CI

```bash
make test          # or: pytest -q
```

Tests run against a tiny embedded fixture corpus (`tests/fixtures/`), so they need
no network and no clone. GitHub Actions runs them on every push/PR
(`.github/workflows/test.yml`, Python 3.11 & 3.12).

## License & credits

Built on [dapytains][dapytains] (DTS/CapiTainS resolver) and [SaxonC][saxonche].
The texts are © their respective editors; see the corpus repository.

[corpus]: https://gitlab.inria.fr/almanach/cllg/freed-corpus
[dapytains]: https://pypi.org/project/dapytains/
[saxonche]: https://pypi.org/project/saxonche/
