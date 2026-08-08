# mtbls-agent — Session Restart Guide

## Project
`/Users/frederikkaempchen/projects/metabolites-metadata-skill`

**Goal:** AI-native search engine for MetaboLights datasets. A pi skill that helps researchers find datasets matching their experimental requirements using natural language, with smart scoring, parallel deep inspection, and comprehensive comparison summaries.

## Environment

```bash
cd /Users/frederikkaempchen/projects/metabolites-metadata-skill
source .venv/bin/activate
```

- Python: 3.14 (uv-managed virtualenv)
- Package manager: `uv pip install ...`
- Key dep: `metabolights-utils>=1.4.35`
- MetaboLights API base: `https://www.ebi.ac.uk/metabolights/ws3`
- Search endpoint: `POST /public/v2/public-study-index/search`
- Package installed in dev mode: `uv pip install -e .`

## Project Structure

```
metabolites-metadata-skill/
├── agents.md              # THIS FILE
├── pyproject.toml
├── SKILL.md                # Pi skill instructions
├── src/
│   └── mtbls_agent/
│       ├── __init__.py     # Public API exports
│       ├── models.py       # Dataclass models
│       ├── client.py       # HTTP client for v2 search API
│       ├── searcher.py     # Phase 1: broad search
│       ├── inspector.py    # Phase 2: deep ISA inspection (parallel)
│       ├── scorer.py       # Hard filters + soft scoring
│       ├── summarizer.py   # Comparison table builder
│       └── workflow.py     # End-to-end pipeline
└── tests/
```

## Current Status

### ✅ Implemented
- [x] Project scaffolding (pyproject.toml, package structure)
- [x] Virtual environment with uv + metabolights-utils
- [x] v2 MetaboLights API integration
- [x] models.py — all dataclass models
- [x] client.py — search API wrapper + hit parsing
- [x] searcher.py — Phase 1 broad search with filters
- [x] inspector.py — Phase 2 parallel HTTP download + ISA-Tab parsing
- [x] scorer.py — Hard requirement filters + nice-to-have scoring
- [x] summarizer.py — Comparison table builder
- [x] workflow.py — find_datasets() convenience wrapper
- [x] SKILL.md — Pi skill instructions

### 📝 Future
- [ ] Advanced MS/compound filters
- [ ] Unit tests
- [ ] BioBERT sample embeddings (Phase 3)
- [ ] Paper connector (Phase 3)

### ✅ Recent Fixes (important!)
- [x] SSL retry on HTTP ISA downloads (3 attempts, backoff)
- [x] Recursive FILES/ directory listing (handles FILES/RAW_FILES/, FILES/DERIVED_FILES/)
- [x] Assay-based sample→file mapping (parses Raw/Derived Spectral Data File columns)
- [x] Download retry with exponential backoff
- [x] Compound extension detection (.d.zip), size parsing from HTML, sample name inference

## Performance

| Operation | Before (FTP) | After (HTTP) |
|-----------|-------------|--------------|
| Single study deep inspect | 5-15s | ~0.5s |
| 5 studies parallel | ~60s+ | ~0.7s |
| 10 studies parallel | ~120s+ | ~1.5s |

The key insight: ISA-Tab files are tiny text files (~15-200KB). HTTP downloads them in ~100ms each vs FTP's multi-second connection overhead.

The bottleneck is now parsing the ISA files (CPU), not downloading them (I/O).

## API Quick Reference

### search_studies(query, ...) → list[StudyCandidate]
Phase 1 broad search. Returns shallow candidates from search index.

### inspect_studies(candidates, max_workers=10) → list[StudyCandidate]
Phase 2 parallel deep-dive. Downloads + parses ISA-Tab via FTP.

### score_studies(candidates, profile) → list[ScoredCandidate]
Hard filters + nice-to-have scoring. Returns sorted by score.

### build_comparison_table(scored, profile) → ComparisonReport
Structured table + candidate details.

### find_datasets(query, profile, ...) → ComparisonReport
End-to-end: search → inspect → score → summarize.

## Key Design Decisions

1. Free text focus — NL parsed by agent into RequirementProfile
2. Iterative search — broad → judge → deep → score → iterate
3. Hard + nice-to-have — pass/fail + scored 0-1
4. No caching — fresh lookups
5. Mixed summary — structured table + AI narrative
6. Parallel deep inspection — ThreadPoolExecutor
7. No CLI — library API for LLMs to script against
8. Sync API — threads handle parallelism internally

## Troubleshooting

- **FTP timeout**: Some studies aren't on public FTP. Inspector falls back to shallow mode gracefully.
- **REST fallback**: If FTP fails, inspector tries REST API. May fail for very old studies.
- **Missing deps**: Run `uv pip install -e .` to reinstall.
### Sample Description Generation (Option D — recipe-based)
- ONE LLM call per study: `build_study_profile_prompt()` → the LLM studies the
  metadata layout (columns, rows, file-name codes, factors, abstract) and
  returns a JSON profile with: `codes` (disease/group decode), `sentence_template`
  (named slots), `slot_sources` (slot→source map), `qc_string`, `study_context`.
- `parse_study_profile()` parses it; `apply_recipe(ctx, profile)` fills each
  sample deterministically — ZERO per-sample LLM calls.
- Disease is decoded from data-file name codes (e.g. ALZ → Alzheimer's) via the
  `{disease}` slot with `{"type": "code", "field": "data_files"}`.
- `SampleSentencesStore` caches per-study results (key = study_id + data hash).
- New file: `src/mtbls_agent/sample_gen.py` (replaced per-sample LLM approach).

### Robustness / real-world gaps fixed (from agent feedback)
- **Offline loader**: `load_study_from_isa(study_id, isa_dir)` reconstructs a
  deep StudyCandidate from LOCAL ISA files (no network). Same result as
  `inspect_studies()`. Exposed via package top-level import.
- **QC enforced in library**: QC/reference/dilution/blank/instrument-
  conditioning/data-dependent-acquisition/emergency/solvent samples ALWAYS get
  `qc_string` — `apply_recipe` never decodes a disease for them (even if they
  link to ALZ_ files). `_is_qc()` markers are listable.
- **Unresolved disease is visible**: non-QC samples with no linked files (or a
  code the glossary missed) surface `disease=unresolved` in
  `SampleDescription.used_sources` instead of failing silently.
- **One sentence generator**: `sample_summarizer.py` was deleted; `sample_gen.py`
  is the only path. `SampleManifest` is typed to `SampleDescription`.
- **Docs**: removed duplicate Step 7, fixed Step 6c dup import, added venv
  rebuild steps.
- **venv**: uv-managed `.venv`; never use homebrew python (broken pyexpat).
  Rebuild: `rm -rf .venv && uv venv && uv pip install -e .`

## Streamlining (latest)
- **One import surface**: `from mtbls_agent import ...` only. Slimmed `__all__` from
  32 → 23; dataclasses are returned by functions, not constructed.
- **Per-sample sentences = one LLM round-trip**: `prepare_samples(deep, store)`
  → `SampleTask` (builds the single profile prompt + contexts).
  `submit_samples(task, llm_text)` applies + caches. `load_samples(task)` reads cache.
  No more collect→prompt→parse→apply→store juggling.
- **`find_datasets` cleaned** (removed `__import__` hack; clean no-profile path).
- **SKILL.md** rewritten to: The One Flow (copy-paste) + an API table + short
  "what to decide" list. Gates A–I prose removed.
- **Parallel-test note**: `~/projects/test-mtbls-meta-skill/metabolites-metadata-skill/`
  is a SEPARATE codebase the user is testing in parallel with its own `.venv`.
  Do NOT edit it. This directory `~/projects/metabolites-metadata-skill/` is ours;
  `.venv` here resolves to this `src/`. If `import mtbls_agent` resolves elsewhere,
  run `uv pip install -e .` in THIS directory.

## Recent fixes (from parallel-agent bug report)
- [#1 linking] manifest prefers Sample Name over Source Name (HuMet WCQA-* vs
  numeric); regression test added (tests/test_sample_linking.py).
- [#2 factor codes] `code` slot now decodes factor values too (OGTT/OLTT/PAT/SLD),
  not just filenames; prompt updated so recipes can point `code` slots at factors.
- [#5 retries] inspector HTTP retries hardened (backoff + jitter, 4 attempts).
- [#6 categories] `_categorize` is directory-aware: mzML under RAW_FILES = raw.
- [#7 cache] `revise_samples(task, text)` auto-bumps revision -> separate cache
  slot; old wording preserved. Cache key includes revision (rN).
- [#4 formats] `format_summary(candidate)` -> {fmt: count} quick probe. MS1/MS2
  still requires opening a file/paper (search index doesn't expose it).
- [#3 env] private venv `.venv-local` isolates this project from the shared
  editable-install jousting with the parallel-test copy. Tests: run
  `.venv-local/bin/python -m pytest tests/ -q`.

## Deterministic discovery (added)
- `profile_to_search_args(profile)` - hard reqs -> search-API filters (server-side).
- `screen_candidates(candidates, profile)` - deterministic shallow hard-pass +
  rank on search-index data only (no network/LLM). Ionization + data formats are
  deliberately NOT screened (need deep data) -> enforced post-inspection.
- `find_datasets` rewired: API-filter -> shallow screen -> deep-inspect ONLY
  survivors -> full score -> report. `report.screening` exposes survivors/dropped.
- Discovery is 100% deterministic once a structured RequirementProfile exists;
  the LLM is only needed for prose->profile and the per-study sentence recipe.
- Tests: tests/test_deterministic.py (4: server args, screen-drop, shallow-ignore
  ionization/formats, shallow rank). Run: .venv-local/bin/python -m pytest tests/ -q
