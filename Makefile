# CLLG simple viewer -- install, run, index, test.
#
#   make install   create the venv, install deps, clone the corpus texts
#   make run       serve with gunicorn (production)
#   make dev       serve with the Flask dev server
#   make index     build the optional full-text search index
#   make test      run the test suite
#   make clean     remove the venv, the index and the cloned corpus

# --- configuration (override on the command line, e.g. `make run PORT=9000`) ---
CORPUS_REPO ?= https://gitlab.inria.fr/almanach/cllg/freed-corpus.git
CORPUS_DIR  ?= corpus
CORPUS_DEPTH ?= 1
VENV        ?= .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
HOST        ?= 127.0.0.1
PORT        ?= 8000
WORKERS     ?= 2
DB          ?= var/search.sqlite

export CLLG_ROOT := $(CURDIR)/$(CORPUS_DIR)/data
export CLLG_DB   := $(CURDIR)/$(DB)

.PHONY: install venv deps corpus run dev index test clean help

help:
	@sed -n '3,9p' $(MAKEFILE_LIST)

## install: venv + dependencies + corpus clone
install: venv deps corpus
	@echo "Done. Browse the texts with:  make run   (or: make dev)"

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)

deps: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

## corpus: shallow-clone the CLLG texts into ./corpus
corpus:
	@if [ -d "$(CORPUS_DIR)/.git" ]; then \
		echo "corpus already present in $(CORPUS_DIR) (git pull to update)"; \
	else \
		echo "cloning $(CORPUS_REPO) -> $(CORPUS_DIR)"; \
		git clone --depth $(CORPUS_DEPTH) "$(CORPUS_REPO)" "$(CORPUS_DIR)"; \
	fi

## run: production server (gunicorn). Set CLLG_FULLTEXT=1 to enable full-text.
run:
	$(VENV)/bin/gunicorn wsgi:app --bind $(HOST):$(PORT) --workers $(WORKERS)

## dev: Flask development server
dev:
	$(PY) browse.py serve --root "$(CLLG_ROOT)" --host $(HOST) --port $(PORT)

## index: build the optional SQLite FTS5 full-text search engine (parallel)
index:
	$(PY) browse.py index --root "$(CLLG_ROOT)" --db "$(DB)" $(if $(JOBS),--jobs $(JOBS),)

## test: run the test suite (uses the embedded fixture corpus, no clone needed)
test:
	$(PY) -m pytest -q

clean:
	rm -rf $(VENV) var/*.sqlite $(CORPUS_DIR)
