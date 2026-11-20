# Cortex

*A universal project knowledge-base documentation system · Compiles codebases into structured, searchable, traceable knowledge bases*

[English](README.en.md) · [简体中文](README.md)

[![CI](https://img.shields.io/github/actions/workflow/status/handar-code/Cortex/ci.yml?branch=main&label=CI&logo=github)](https://github.com/handar-code/Cortex/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Cortex compiles a codebase into a structured knowledge base — symbol catalog, attribution, version snapshots, and vector index — for agent retrieval with direct evidence chains. Project structure, languages, triage rules, and governance are driven by `config.yaml`, with no project-specific hardcoding.

Installing from source provides the `cortex` command.

---

## Overview

Agents taking over an unfamiliar codebase typically read source directly, which is costly and offers limited traceability. Cortex compiles a codebase into a structured knowledge base that returns symbols, snippets, and provenance on retrieval.

```
codebase ──ingest──▶ symbol catalog ──▶ attribution (@domain) ──▶ vector index
                       │                     │                       │
                       └─ incremental + snapshots └─ dual-track       └─ three channels side by side
                                          ▼
                              agent retrieval (structured evidence chain)
```

**Core features**:

1. **Dual-track attribution** — `@domain` explicit tags ‖ implicit vector soft-assignment, two equal, parallel tracks. Domain assignment does not depend on hand-written tags; directory scope and implicit vector assignment provide attribution when tags are absent.
2. **Three channels side by side** — retrieval returns `annotation_hits` (explicit keys) / `annotation_text_hits` (docstring terms) / `vector_hits` (vector neighbors), without pre-fusion; the agent adjudicates.
3. **git-style versioning** — every recompute is a commit with a mandatory `change_reason`, rollback support, `active` marking the published version, and detects drift between code and catalog.
4. **Zero-config runnable** — the full pipeline runs offline, key-free, and without human intervention.

**Measured figures**: a ~10k-file project → 401 files / **10,512 symbols** / 10 domain shards / ~3.3s / traceability coverage 1.0.

## Capabilities at a glance

| Capability | Description |
|---|---|
| **Multi-language symbol extraction** | Python AST extraction; Markdown full text + comments (`#` / `//` / `/* */` / `<!-- -->`) as symbols; TypeScript extractor is a stub, extensible via the `extract/` adapter contract |
| **Determinism first** | Built-in triage + built-in extraction are the main path; AI extraction is a fallback upgrade |
| **Two vector tiers** | `offline` deterministic hashing (zero-dep, CI/offline-ready) / OpenAI-compatible API client (local model servers and cloud share **one codebase**, switch via `api_base`) |
| **ANN backends** | `flat` brute-force cosine (zero-dep default) / `hnsw` approximate nearest neighbor (`pip install ".[ann]"`), orthogonal to the embedder and freely combinable |
| **Query-time reranking** | corpus indexed with offline hashing, rerank with bge-m3 at query time (`retrieval.rerank: siliconflow`), pay-per-use, zero extra offline cost |
| **Render artifacts** | MkDocs site (for humans) + Markdown/llms.txt shards (for agents); optional artifacts off by default |
| **Read/write separation** | read = Skill guide (no writes) / write = MCP git-style commits, physically separated |
| **Implicit contract defect detection** | `cortex defects` statically detects contract breaks; catalog records call relationships, defects reports violations |

## Dependencies

**Core dependency**:

```
pyyaml>=6.0
```

The full pipeline (ingest → attribution → render → index → retrieve) runs **with zero third-party dependencies**; embedding uses the built-in `offline` deterministic hash, requiring no network and no key.

**Optional dependencies**:

| Group | Dependencies | Purpose |
|---|---|---|
| `ann` | `hnswlib>=0.8.0`, `numpy>=1.23` | Approximate nearest neighbor backend for large-scale vector retrieval |
| — | none | True semantic vectors (local model servers / cloud): standard-library `urllib` implements OpenAI-compatible `POST /v1/embeddings`, **no extra dependencies** |

```bash
pip install -e .                 # minimal: core features, enough for the offline pipeline
pip install -e ".[ann]"          # optional: add hnswlib + numpy for the hnsw backend
```

- **Python**: >= 3.10 (CI covers 3.10 / 3.11 / 3.12 / 3.13)
- **System requirements**: none. Pure Python, no toolchain (unless installing `ann`, where hnswlib needs a wheel or build environment)
- **External service**: **not needed by default**. `offline` is fully offline; true semantics needs an OpenAI-compatible `/v1/embeddings` endpoint (local Ollama / llama.cpp / LM Studio / vLLM, or cloud SiliconFlow)

---

## Quick index

| Task | Command or config |
|---|---|
| Run the pipeline locally (no key) | "5-minute Quickstart" + `config.quickstart.yaml` (offline) |
| Production deployment, true semantic retrieval | `config.example.yaml` (siliconflow / BAAI/bge-m3) + `SILICONFLOW_API_KEY` |
| Speed up vector search on large projects | `--ann-provider hnsw` or set `ann_provider: hnsw` (requires `pip install ".[ann]"`) |
| Verify a change didn't break anything | `pip install -e ".[ann,dev]" && pytest -q` (60 passed, all green without a key); GitHub Actions runs on push |
| List all commands | `cortex --help` · "Full CLI reference" |
| Report a security issue | [`SECURITY.md`](SECURITY.md) (private vulnerability report; do not open a public Issue) |

**Key concepts**:
- **Embedder tiers** = `offline` (deterministic hash) / OpenAI-compatible API client (`siliconflow` cloud or local server). Determines how corpus vectors are computed.
- **ANN backend** = `flat` (brute-force cosine) / `hnsw` (hnswlib approximate neighbor). Determines nearest-neighbor retrieval at query time, orthogonal to the embedder.
- **Three channels** = retrieval returns `annotation_hits` (`@domain` keys) / `annotation_text_hits` (docstring terms) / `vector_hits` (vector neighbors), without pre-fusion.

---

## 5-minute Quickstart (zero config · no API key · offline)

This flow uses `offline` deterministic hashing — no key, no network, reproducible results. A synthetic sample project is provided at `fixtures/sample_project/`.

```bash
# 1) Install (Python >= 3.10)
pip install -e .                 # minimal: only pyyaml, enough for the offline pipeline
# pip install -e ".[ann]"        # optional: add hnswlib + numpy for the hnsw backend
# pip install -e ".[ann,remote]" # full (remote=requests, for the true-semantic path)

# 2) Prepare an artifact directory in any working dir (config references the bundled quickstart config by absolute path)
CFG=/path/to/cortex/config.quickstart.yaml
mkdir -p demo && cd demo

# 3) Ingest: scan → triage → extract → attribute → catalog snapshot
cortex ingest --config "$CFG" --out catalog.json
#    [ingest] project=quickstart-sample scanned=6 valid=5 symbols=18

# 4) git-style catalog storage: init → first recompute commit → CI publish (atomic active switch only if gates pass)
cortex store-init --config "$CFG" --store .docstore
cortex recompute  --config "$CFG" --store .docstore --reason "init catalog v1"
cortex ci-publish --config "$CFG" --store .docstore --reason "release v1"

# 5) Build index (three-piece set + vector neighbors)
cortex index --config "$CFG" --store .docstore --out .doc_system/index/index.json
#    [index] entries=18 provider=OfflineEmbedder ann=flat

# 6) Retrieve: three channels returned side by side
cortex retrieve --config "$CFG" --store .docstore --query "user login" --k 3
```

To try the `hnsw` backend (requires `.[ann]`):

```bash
cortex index --config "$CFG" --store .docstore --ann-provider hnsw \
             --out .doc_system/index/hnsw.json
# Also writes hnsw.hnsw + hnsw.meta.json; next load is zero-rebuild
```

> **Relationship between `config.quickstart.yaml` and `config.example.yaml`**: the former is a "zero-config smoke test" with both `embedding.provider` and `attribution.provider` set to `offline`; the latter shows the **production default** (`siliconflow` / BAAI/bge-m3 true semantics, requiring `SILICONFLOW_API_KEY`). After the Quickstart, copy `config.example.yaml` to `config.yaml`, adjust `roots` as needed, and put the key in an environment variable or project-root `.env` (excluded by `.gitignore`, never committed) to upgrade to true semantics.

---

## Two embedder tiers: how corpus vectors are computed

`index.get_embedder(cfg)` selects based on `config.yaml`'s `embedding.provider`; the CLI can override with `--provider`.

| Tier | provider | Implementation | Dim | Dependencies | Semantics | Use case |
|---|---|---|---|---|---|---|
| Tier0 | `offline` (default) | `OfflineEmbedder` deterministic hash | 256 | none (stdlib only) | none (term hash) | offline / CI / no key / reproducible fallback |
| Tier1 | `siliconflow` (can point local) | `SiliconFlowEmbedder` · BAAI/bge-m3 | 1024 | HTTP client | true semantics, cross-lingual synonyms | production ingest: full corpus true semantics |

**Local and cloud share one interface**: local model servers (Ollama / llama.cpp server / LM Studio / vLLM) and cloud (SiliconFlow) expose the same OpenAI-compatible interface — `POST /v1/embeddings`, with identical request `{model, input, encoding_format}` and response `{data:[{index, embedding}]}`. No separate local embedder is required; change `api_base` in `config.api.yaml`:

```yaml
# Local endpoint
embedding:
  provider: siliconflow
  api_base: http://localhost:11434/v1/embeddings   # Ollama; llama.cpp uses http://localhost:8080/v1/embeddings
  model: bge-m3                                     # use bge-m3 locally too, to keep 1024 dims
  api_key_env: SILICONFLOW_API_KEY                  # local servers don't check tokens; a placeholder is fine
```

A complete runnable config is at [`config.api.yaml`](config.api.yaml) (comments show which lines to change for local vs cloud). **Keep bge-m3 locally to preserve 1024 dims** — otherwise `SiliconFlowEmbedder.dim=1024` won't match the actual vector dimension and hnsw build will fail.

**Layering rationale**: `offline` guarantees the full pipeline runs at zero cost in any environment (CI, no network, fresh clone) — the "floor you can always fall back to"; the true-semantic tier provides semantic quality (measured cross-lingual synonym cosine ~0.81–0.87 vs unrelated ~0.44; offline hashing is always 0 and cannot distinguish). SiliconFlow is currently free and unthrottled, suitable for embedding the whole corpus at once.

**Batch + concurrency**: `embed_many(texts)` groups requests by `batch_size`, runs multiple batches concurrently with `ThreadPoolExecutor` (`max_concurrency`, default 8), and reorders results by both `data[].index` and batch position so output order matches input; failures raise a clear `RuntimeError`. Long text is truncated to `MAX_EMBED_CHARS=6000` to avoid API rejection from bge-m3's context limit (catalog still stores the full text; only embedding input is truncated).

**API keys never enter the repo**: read from the `SILICONFLOW_API_KEY` environment variable or project-root `.env` (`Config._load_local_env`); `config.yaml` holds only `model / api_base / api_key_env / batch_size / max_concurrency`, never a plaintext key.

**Query-time reranking**: if the corpus is indexed with `offline` but you want true semantics at query time, set `retrieval.rerank: siliconflow`: recall top-N candidates first, then rerank them with bge-m3 to pick the top k — pay-per-use, zero extra offline cost. If the corpus itself is a bge-m3 true-semantic index and queries also use bge-m3 directly, ranking is already true semantic → reranking is redundant and should be `null`.

---

## ANN backends: finding nearest neighbors at query time

Orthogonal to the embedder: the embedder decides what the vectors are, the ANN backend decides how to find top-k among N vectors. Configured via `ann_provider` / overridden by `--ann-provider`.

| Backend | Implementation | Complexity | Dependencies | Features |
|---|---|---|---|---|
| `flat` (default) | `FlatBackend` brute-force cosine scan | O(n) | none | exact, deterministic, reproducible, zero-dep fallback |
| `hnsw` | `HnswBackend` · hnswlib cosine space | approximate logarithmic | `hnswlib>=0.8.0` + `numpy` | faster retrieval on large projects; clear install hint if missing, does not pollute the flat path |

- Abstracted as a `VectorBackend` base class: `build(vectors)` / `query(vec, k) -> [(score, idx)]` / `save_index` / `load_index`. `retrieve()`'s **external signature and return structure are unchanged** — only the underlying nearest-neighbor computation differs.
- `hnsw` supports **cross-process persistence**: `save()` additionally writes `<stem>.hnsw` + `<stem>.meta.json` (recording `dim` / `n`), so the next `KnowledgeIndex.load(ann_provider="hnsw")` calls `load_index` directly — zero rebuild, zero API cost.
- To switch an existing `index.json` to hnsw without recomputing vectors, run `python scripts/rebuild_hnsw.py` to reuse existing vectors.
- On small datasets `hnsw` and `flat` top-k sets fully overlap (sufficient `ef=n` recall), locked by `tests/test_phases.py::ANNBackendTest`.

---

## Full CLI reference (production config `config.yaml`)

```bash
# 1) Scan → triage → extract → catalog snapshot
cortex ingest --config config.yaml --out catalog.snapshot.json

# 2) Init git-style catalog store + first recompute commit
cortex store-init --config config.yaml
cortex recompute  --config config.yaml --reason "init catalog v1"

# 3) CI publish (atomic active switch if gates pass; the only publishing authority)
cortex ci-publish --config config.yaml --reason "release v1"

# 4) Render (MkDocs source + llms.txt shards) + index
cortex render --config config.yaml --out .doc_system
cortex index  --config config.yaml --out .doc_system/index/index.json
cortex index  --config config.yaml --ann-provider hnsw --out .doc_system/index/index.json
cortex retrieve --config config.yaml --query "user account and login" --k 3
cortex retrieve --config config.yaml --query "user login" --rerank siliconflow --k 3

# 5) Read-side artifacts (no writes)
cortex gen-skill     --out .doc_system
cortex gen-agents-md --out .doc_system
cortex onboard       --out .doc_system

# 6) Governance
cortex gate
cortex probe  --config config.yaml
cortex jury   --question "is the KB helpful?"
cortex bootstrap --agents-md path/to/AGENTS.md --out .doc_system/constitution.json

# Drift fallbacks
cortex drift     --config config.yaml
cortex reconcile --config config.yaml          # build candidate version (not published)
cortex rollback  --version v1                 # roll back active
```

## Tests and CI

```bash
pytest -q                 # 60 passed, no API key needed
python -m unittest discover -s tests   # equivalent (unittest style also works)
```

- **No-key mechanism**: `tests/conftest.py`, when `SILICONFLOW_API_KEY` is unset, forces both `index` and `attribution` `get_embedder` to `OfflineEmbedder` at import time; when a key is set it keeps the real provider, also validating the true-semantic path. Placing this at import time (rather than a function-level fixture) covers `unittest.TestCase.setUpClass`.
- `tests/test_integration_e2e.py`: offline full pipeline (ingest → flat/hnsw dual index → retrieval), asserting vector/annotation-text dual-channel hits and flat-hnsw top-5 overlap ≥2.
- **GitHub Actions** (`.github/workflows/ci.yml`): triggered on push to `main` + PRs, Python 3.10 / 3.11 / 3.12 / 3.13 matrix, `pip install .[ann]` then `pytest tests -q`. To validate the true-semantic path, configure a `SILICONFLOW_API_KEY` secret and uncomment the `env` block in ci.yml.
- `pyproject.toml`'s `testpaths = ["tests"]` prevents the sample `.py` files in `fixtures/sample_project/` from being collected as tests.

Security policy: [`SECURITY.md`](SECURITY.md) (private vulnerability report).
