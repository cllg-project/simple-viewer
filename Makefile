# CLLG simple viewer -- install, run, index, test.
#
#   make install   create the venv, install deps, clone the corpus texts
#   make run       serve with gunicorn (production; FULLTEXT=1 VECTORS=1 to enable)
#   make dev       serve with the Flask dev server
#   make index     build the optional full-text search index
#   make search    build the index AND serve with full-text search enabled
#   make vectorize build the optional semantic "Similar passages" vector store
#   make similar   build the vector store (if needed) AND serve with it enabled
#   make test      run the test suite
#   make clean     remove the venv, the indexes and the cloned corpus

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
VECTORS_DIR ?= var/vectors
VECTORS_DB  ?= $(VECTORS_DIR)/vectors.sqlite
MODEL_DIR   ?= var/models/AncientGreekVariantSBERT-ONNX

# CLLG_VECTORS is the store *directory* (cllg_viewer appends vectors.sqlite).
export CLLG_ROOT    := $(CURDIR)/$(CORPUS_DIR)/data
export CLLG_DB      := $(CURDIR)/$(DB)
export CLLG_VECTORS := $(CURDIR)/$(VECTORS_DIR)

.PHONY: install venv deps deps-vectors deps-vectors-gpu corpus run dev index \
        search vectorize similar test clean help

help:
	@sed -n '3,11p' $(MAKEFILE_LIST)

## install: venv + dependencies + corpus clone
install: venv deps corpus
	@echo "Done. Browse the texts with:  make run   (or: make dev)"

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)

deps: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

## deps-vectors: build-only ML deps for `vectorize` (CPU onnxruntime, tokenizers, ...)
deps-vectors: venv
	$(PIP) install -r requirements-vectors.txt

## deps-vectors-gpu: build-only ML deps with CUDA onnxruntime-gpu (needs CUDA/cuDNN)
deps-vectors-gpu: venv
	$(PIP) install -r requirements-vectors-gpu.txt

## corpus: shallow-clone the CLLG texts into ./corpus
corpus:
	@if [ -d "$(CORPUS_DIR)/.git" ]; then \
		echo "corpus already present in $(CORPUS_DIR) (git pull to update)"; \
	else \
		echo "cloning $(CORPUS_REPO) -> $(CORPUS_DIR)"; \
		git clone --depth $(CORPUS_DEPTH) "$(CORPUS_REPO)" "$(CORPUS_DIR)"; \
	fi

## run: production server (gunicorn). Enable layers: make run FULLTEXT=1 VECTORS=1
run:
	CLLG_FULLTEXT=$(if $(FULLTEXT),1,) CLLG_VECTORS_ON=$(if $(VECTORS),1,) \
		$(VENV)/bin/gunicorn wsgi:app --bind $(HOST):$(PORT) --workers $(WORKERS)

## dev: Flask development server
dev:
	$(PY) browse.py serve --root "$(CLLG_ROOT)" --host $(HOST) --port $(PORT)

## index: build the optional SQLite FTS5 full-text search engine (parallel)
index:
	$(PY) browse.py index --root "$(CLLG_ROOT)" --db "$(DB)" $(if $(JOBS),--jobs $(JOBS),)

## search: build the index (if needed) then serve with full-text search enabled
search:
	@test -f "$(DB)" || $(MAKE) index
	$(PY) browse.py serve --root "$(CLLG_ROOT)" --host $(HOST) --port $(PORT) \
		--fulltext --db "$(DB)"

## vectorize: build the optional semantic "Similar passages" vector store.
## Reuse already-rendered FTS text (skip Saxon):  make vectorize FROM_FTS=$(DB)
vectorize: deps-vectors
	$(PY) browse.py vectorize --root "$(CLLG_ROOT)" --vectors-db "$(VECTORS_DB)" \
		--model-dir "$(MODEL_DIR)" $(if $(JOBS),--jobs $(JOBS),) \
		$(if $(FROM_FTS),--from-fts "$(FROM_FTS)",)

## similar: build the vector store (if needed) then serve with it enabled
similar:
	@test -f "$(VECTORS_DB)" || $(MAKE) vectorize
	$(PY) browse.py serve --root "$(CLLG_ROOT)" --host $(HOST) --port $(PORT) \
		--vectors --vectors-db "$(VECTORS_DB)"

## test: run the test suite (uses the embedded fixture corpus, no clone needed)
test:
	$(PY) -m pytest -q

clean:
	rm -rf $(VENV) var/*.sqlite var/vectors var/models $(CORPUS_DIR)
