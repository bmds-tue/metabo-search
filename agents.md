# metabo-search — Session Restart Guide

## Project
`metabo-search` (this repository)

**Goal:** AI-native search engine for MetaboLights datasets. A pi skill that helps researchers find datasets matching their experimental requirements using natural language, with smart scoring, parallel deep inspection, and comprehensive comparison summaries.

(Note: package module is `metabo_search`, distribution is `metabo-search`.)

## Environment

```bash
./scripts/install.sh       # from the repo root: creates .venv-local, pip-installs -e ., links skills
scripts/python -c "import metabo_search; print(metabo_search.__file__)"
```

- Private venv: `.venv-local` (isolates from the shared `.venv` / parallel-test copy)
- Package manager: `uv pip install --python .venv-local/bin/python ...`
- Key dep: `metabolights-utils>=1.4.35`
- MetaboLights API base: `https://www.ebi.ac.uk/metabolights/ws3`
- Search endpoint: `POST /public/v2/public-study-index/search`
- Rebuild: `uv venv .venv-local && uv pip install --python .venv-local/bin/python -e .`

## Project Structure

```
metabo-search/
├── AGENTS.md              # THIS FILE
├── pyproject.toml
├── SKILL.md               # Pi skill instructions
├── docs/                  # browser guide (index.html, api.md)
├── references/api.md      # deep API reference
├── scripts/               # install.sh, uninstall.sh, python, gen_api_docs.py
├── src/metabo_search/
│   ├── __init__.py        # Public API exports
│   ├── models.py          # Dataclass models
│   ├── client.py          # HTTP client for v2 search API
│   ├── searcher.py        # Phase 1: broad search
│   ├── inspector.py       # Phase 2: deep ISA inspection (parallel)
│   ├── scorer.py          # Hard filters + soft scoring + shallow screen
│   ├── summarizer.py      # Comparison table builder
│   ├── sample_gen.py      # Per-sample sentence recipe (1 LLM call/study)
│   ├── downloader.py      # FILES/ listing + selective downloads
│   ├── manifest.py        # SampleManifest export
│   ├── workflow.py        # find_datasets (shim over quick_discovery)
│   ├── repositories/      # per-repository code (seam: repositories/base.py)
│   │   ├── base.py        # StudyRepository ABC, DISPATCH, validate_databases
│   │   ├── metabolights.py# adapter over the top-level MetaboLights modules
│   │   └── workbench/     # Metabolomics Workbench (corpus/matcher/slots/…)
│   ├── core/              # typed pipeline (design: docs/design-pipeline.md)
│   │   ├── __init__.py    # public surface (pipeline, factories, recipes)
│   │   ├── results.py     # 8 result types: to_json/from_json/digest/fmt
│   │   ├── steps.py       # Step, configs, predicates, Pipeline (validate/run/
│   │   │                  #   extend/diff/ladder), thin body adapters
│   │   ├── cache.py       # CacheStore: plan.json + results/, TTLs
│   │   └── recipes.py     # quick_probe/discovery, full_report, harvest
└── tests/                 # deterministic + real-world linking + core
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
- [x] sample_gen.py — deterministic per-sample sentence recipes (1 LLM call/study)
- [x] downloader.py — recursive FILES/ listing + selective downloads
- [x] manifest.py — SampleManifest export
- [x] workflow.py — find_datasets() shim over quick_discovery
- [x] repositories/ — repository seam (base.py: StudyRepository ABC, DISPATCH,
  database_from_id, validate_databases; DEFAULT_DATABASES = metaboliLights +
  workbench). Search step dispatches per db, tags candidates, caches per db.
- [x] workbench/ — Metabolomics Workbench (NIH NMDR) repository: pooled
  httpx client, whole-index corpus (summary + disease/source/species maps,
  7 d TTL, 3× retry), vendored vocab snapshots (259/328 disease/source,
  486 latin/450 common species), rapidfuzz matcher (cutoff 75 + tie-slack 2,
  alias table, abstain-on-ambiguity), metstat slot assembly (structured
  fields only — SPECIES/SOURCE/DISEASE), corpus term-match backstop + species
  screen, parallel deep inspect (factors/analysis/metabolites → assays,
  sample_metadata, metabolite_count), actionable ambiguity notice in
  `SearchResult.fmt()`/`args_used`. Live smoke verified (corpus, slots,
  screening, deep, score). MAF/datatable adapter + describe from factors = next.
- [x] core/ — typed pipeline: results.py (8 result types, to_json/digest/fmt),
  steps.py (Step/Config/Predicate/Pipeline, validate/run/extend/diff),
  cache.py (plan.json + results/, TTLs, warm replay), recipes.py
  (quick_probe/quick_discovery/full_report/harvest). 180 tests green
  (86 pre-existing adapted to databases=("metabolights",) + 30 new workbench/
  database-option tests, offline via METABO_WORKBENCH_FIXTURES).
- [x] SKILL.md — portable Pi/Claude/opencode skill + references/api.md,
  now pipe-first (typed pipeline), legacy table marked

### 📝 Future
- [ ] Advanced MS/compound filters
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

Today the bottleneck is **network** (shared keep-alive client, ~0.5s/study at
12 threads), not CPU: parsing runs on process cores, overlapped under the
download phase. Full measurements + justification: **docs/perf.md**.

## Parallelism (measured, 8 cores / macOS)
- **Workers**: inspect download phase ~2s/study serial → ~0.74s at workers=8; 8→12 is diminishing. Default `workers=10`, sweet spot ≈ cores (8).
- **Parse is GIL-bound**: ~180ms/study CPU; threads give ZERO parse speedup (measured).
- **Process parsing** (`inspect(parse_workers=...)`, auto ON for ≥8 studies): 16 studies parse 2.95s (threads) → 1.39s (4 procs) ≈ 2.1×. **Overlapped**: each download submits its parse the moment it lands (CPU hides under I/O; threads or processes, identical results). Env `MTBLS_PARSE_PROCESSES=0|N` forces off/N; falls back to threads if spawn unavailable. Core: `inspect(parse_workers=...)`.
- **HTTP/2 measured SLOWER** on ftp.ebi.ac.uk (0.83s vs 0.37s) — not used.
- **Real bug bench found**: search-hit `factors` were raw camelCase dicts vs declared `list[OntologyTerm]` → fresh-vs-cached serialization diverged → warm cache keys shifted → inspect re-ran. Fixed in `client.parse_hit` (canonical terms); regression tests in tests/test_client_parse.py.
- Biggest lever remains the screen cap before inspect (deep work is linear in survivors).

## Network at hundreds-of-studies scale (metadata, first run)
- Per study ≈ 1 listing + ~4-6 ISA file GETs. Drive: `httpx.get()` per request = a FRESH TLS connection each time → handshake cost dominates.
- **Implemented**: shared keep-alive `httpx.Client` in inspector + downloader (module-level `_http_client()`); connection reuse across every request/thread. Monotonic win.
- Search API client is already pooled per call (1-2 requests/run) — fine.
- Rejected with measurements (see docs/perf.md): **HTTP/2** (slower), **async** (≈ sync; server caps ≈24 conns), **REST-zip primary** (endpoint 503/404 — HTTP listing+files stays primary, REST stays fallback).
- Re-runs are already ~0s via the step cache (search 7d / inspect 30d).
- Repeated bulk querying can trigger temporary blocks from the API (connection-refused/503 until it recovers) — iterate against a warm cache root (TTL'd) when refining.
- **The cache refuses degraded inspects** (shallow leftovers are logged +
  never cached; re-runs re-inspect) — keep `inspect(workers≈8)` per repo
  (16+ overloads the file server → refusals → shallow candidates), detect
  survivors via `inspection_depth != "deep"`, and recover with the serial
  straggler retry (cookbook Pattern 6). WB `/metabolites` returns an EMPTY
  LIST for the largest studies — count unknown (`metabolite_list_unavailable`),
  never 0.
- Full measurements: `docs/perf.md`.

## Network / parallelism — measured round (36 studies, live)
- Keep-alive: sync 12 threads 36 studies = 19.23s (0.53s/study) — big win over fresh-connection path.
- **Async rejected**: async 16 = 20.1s, async 24 = 18.8s (≈ sync); >32 concurrent connections get refused → server per-host cap ≈ 24, threads+keep-alive already hit the ceiling. No asyncio code in the library.
- **REST-zip primary rejected**: `ws/studies/{id}/download/isa?format=zip` currently 503 (ws3 equivalent 404) — unreliable; HTTP listing+files stays primary, REST remains fallback.
- **Overlap built**: inspect now submits each parse the moment its download lands (CPU parse hides under I/O; threads or processes, same results — tests: tests/test_inspector_process.py).
- **FILES/ listing cache built**: `list_data_files(candidate, cache_dir=)` + `DownloadConfig.files_cache_dir`; the download step wires its cache root (`<root>/files/file_listings/<sid>.json`). Repeat downloads/walks in one cache root = zero listing network (tests: tests/test_downloader_cache.py).
- **Process parsing needs the `__main__` guard**: the process-pool auto path (≥8 studies) uses spawn on macOS — scripts must guard module-level code (`if __name__ == "__main__":`); otherwise children re-execute the whole script (seen live: 8+ parallel pipelines with garbage timings). Library falls back to threads gracefully, but prefer guarding or `MTBLS_PARSE_PROCESSES=0` in ad-hoc scripts. pytest and guarded scripts are fine (determinism test proves proc == thread).
- **By-value handoff fixed (real bug)**: results crossed steps by REFERENCE — inspect's in-place `_merge_enriched` mutated candidates still held by ealier results, so fresh digests ≠ warm digests (cold search payload 924KB vs stored 23KB). The fold now deep-copies at every boundary (stored snapshot stays pristine; the next step gets its own copy). Regression: test_results_cross_steps_by_value.
- API stable: all changes additive (`parse_workers=`, `cache_dir=`, `files_cache_dir=`), defaults preserve behavior.

## Typed pipeline (core/ — the preferred entry point today)

```python
from metabo_search import (pipeline, search, filter, screen, maf, custom,
    inspect, score, describe, download, export, register_predicate,
    CacheOpts, PrintOpts, quick_probe, quick_discovery, full_report, harvest)

p = quick_discovery("urine alzheimer", profile).cache(".pipeline_cache")
r = p.run()                       # r["score"], r.score, r.order — typed mapping
r2 = p.extend(
        describe(top=3)).run(llm=call_llm)   # sentences: 1 LLM call/study, cached
```

- **Repositories (`databases=`)**: `search()`/recipes/`find_datasets` default to
  `databases=("metabolights", "metabolomics_workbench")` — both repositories,
  searched in order, candidates tagged `candidate.repository`. The search step
  caches **per database** (db-scoped keys; TTL + `force` honored), so changing
  ``databases`` never re-runs an unchanged repo. `databases=("metabolights",)`
  is byte-identical to pre-workbench behavior. The workbench needs a cache
  root (`pipeline.cache(root)` or `METABO_WORKBENCH_FIXTURES` for tests): its
  whole-index corpus is fetched once per 7 d and filtered locally afterwards.
- **Vocabulary matching (workbench)**: rapidfuzz + alias table + confidence
  gate; on ambiguity the SLOT STAYS EMPTY (never a guess) and an actionable
  notice lands in `SearchResult.fmt()`/`args_used` telling the agent the next
  step (refine free text → workbench-only re-run → `pipeline(input=...)`
  composition). `StudyRequirements.diseases` feeds the DISEASE slot
  (structured field only — free text is never a slot term).

- Steps are `Config → Result`; step N's output type is step N+1's input type.
- `pipeline(step..., input=SomeResult)` starts midstream / offline; run() also
  accepts `input=` and `force=` (bypass cache).
- Empty config = identity — operations on data do nothing by default. Only
  `search()` requires a query; `download()` requires ≥1 constraint.
- Cache: each step's key = sha(kind + config + input digest); warm steps replay
  from `<root>/results/`, plan recorded in `<root>/plan.json`. TTLs: search 7d,
  inspect 30d, describe eternal (revision-keyed).
- `filter()` predicates: `screen(...)` (SearchResult), `maf(...)`
  (InspectResult), `custom(name, **params)` (registry: register_predicate).
- Custom predicates keep configs plain-data/cacheable (registered by name).
- validate() raises plan-time: type-chain breaks, mis-placed predicates,
  unconstrained download.
- Full design: `docs/design-pipeline.md`.

## API Quick Reference (legacy function surface — still importable)

### search_studies(query, ...) → list[StudyCandidate]
Phase 1 broad search. Returns shallow candidates from the search index.

### inspect_studies(candidates, max_workers=10) → list[StudyCandidate]
Phase 2 parallel deep-dive. HTTP-downloads ISA-Tab and parses it (~0.5s/study).

### load_study_from_isa(study_id, isa_dir) → StudyCandidate
Offline: rebuild deep info from local ISA files, no network.

### screen_candidates(candidates, profile) → ScreeningResult
Deterministic shallow hard-pass on search-index data + soft rank.

### score_studies(candidates, profile) → list[ScoredCandidate]
Hard filters + nice-to-have scoring. Returns sorted by score.

### build_comparison_table(scored, profile) → ComparisonReport
Structured table + candidate details.

### find_datasets(query, profile, ...) → ComparisonReport
End-to-end: search → screen → inspect → score → summarize.

## Key Design Decisions

1. Free text focus — NL parsed by agent into RequirementProfile
2. Iterative search — broad → judge → deep → score → iterate
3. Hard + nice-to-have — pass/fail + scored 0-1
4. Per-step cache — sha(kind+config+input digest) keys, TTLs (search 7d, inspect 30d, describe eternal); warm steps replay from `<root>/results/` (+ plan.json, isa/, files/)
5. Mixed summary — structured table + AI narrative
6. Parallel: thread download + process/thread parse **overlapped** (parse_workers auto ≥8 studies)
7. No CLI — library API for LLMs to script against
8. Sync API — threads handle parallelism internally

## Troubleshooting

- **HTTP download failure**: Some studies aren't reachable on the public mirror.
  The inspector retries with backoff+jitter, then REST-ZIP fallback, then keeps the
  shallow version gracefully.
- **Missing deps**: `uv pip install --python .venv-local/bin/python -e .` to reinstall.
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
- New file: `src/metabo_search/sample_gen.py` (replaced per-sample LLM approach).

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
- **One import surface**: `from metabo_search import ...` only. `__all__` = 64 exports
  (legacy functions + the typed pipeline surface); dataclasses are returned by
  functions, not constructed.
- **Per-sample sentences = one LLM round-trip**: `prepare_samples(deep, store)`
  → `SampleTask` (builds the single profile prompt + contexts).
  `submit_samples(task, llm_text)` applies + caches. `load_samples(task)` reads cache.
  No more collect→prompt→parse→apply→store juggling.
- **`find_datasets` cleaned** (removed `__import__` hack; clean no-profile path).
- **SKILL.md** rewritten to: The One Flow (copy-paste) + an API table + short
  "what to decide" list. Gates A–I prose removed.
- **Parallel-test note**: the user keeps a separate codebase (with its own
  `.venv`) for parallel testing. Do NOT edit it. In THIS repository, `.venv`
  resolves to this `src/`. If `import metabo_search` resolves elsewhere, run
  `uv pip install -e .` in THIS directory.

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

## Real-world linking regression tests (tests/test_linking_realworld.py)
Fixtures from 5 real studies (no network): MTBLS719 (dementia urine ALZ_*),
MTBLS1375 (LipidCreator flat FILES/), MTBLS78 (nested LCMS_Co-culture .raw),
MTBLS640 (NMR bare 1.zip), MTBLS1333 (punctuation-laden *_fip.tsv).
Coverage:
- Token fallback: A-1 != A-2 (multiset/Counter), Pos != Neg, replicate bounds,
  punctuation names; cryptic ALZ names must NOT false-link (assay map is primary).
- Assay-map linking: manifest + collect_sample_contexts attach real ALZ files by
  Sample Name (DCR00004_U) with Source Name differing.
- detect_ext / infer_sample_name / categorize on real names and dirs
  (RAW_FILES beats .mzml; DERIVED_FILES; flat co-culture by ext; .tsv -> other).
- Recipe decodes real ALZ code -> "Alzheimer's disease".

## Bugs these tests found while being written
- _sample_matches false-positive: 1_LTR_1_A-1 matched 1_LTR_1_A-2 (set-based
  tokens lost multiplicity) -> switched to multiset (Counter) subset.
- _sample_matches missed 1_LTR_1_A-1.d.zip (only last suffix stripped,
  "1.d" glued) -> strip ALL Path suffixes before tokenizing.

## Run tests
`.venv-local/bin/python -m pytest tests/ -q`  (180 passed currently)
NOTE: manifest.py was corrupted by a bad sed once - rebuilt cleanly; keep the
single-module invariant (grep -c "def _sample_matches" manifest.py == 1).

## MAF (metabolite assignment) support
- **What**: `m_*.tsv` ISA files — one row per identified metabolite (name,
  formula, m/z, RT, database, per-sample abundance).  Parsed in
  `_parse_maf_files` into `StudyCandidate.metabolite_count` +
  `maf_files_parsed`; surfaced via `load_study_from_isa` / `inspect_studies`.
- **Gotcha fixed**: real MAFs (e.g. MTBLS1375) leave `database_identifier`
  empty and put the name in `metabolite_identification` — the counter now uses
  the fullest populated column, not the first one (regression-test `test_maf.py`).
- **Filter**: `filter_by_maf(cands, require_maf=True, min_metabolites=N)` —
  post-inspection (search index has no MAF).  `StudyRequirements` gained
  `has_maf` (True/False) + `min_metabolites`; wired as hard/nice criteria in
  `_score_one`, enforced after deep inspection like ionization/formats.
- **Download**: `download_maf_files(study_id, dest)` fetches only the
  `m_*.tsv` files; ``{dest}/{study_id}/`` layout, same retry logic.
- **Analysis (LLM-free)**: `maf.py` — `analyze_maf_files(id, isa_dir|maf_paths)`
  → `MafAnalysis` list: `metabolite_count` (fullest populated column),
  `sample_count` + `sample_columns` (non-metadata cols), `named_count` /
  `identified_count` / `mz_count`, `annotation_level`
  (`'named'|'identified'|'mz_only'|'empty'`, name > identifier > m/z precedence),
  `examples`.  `render_maf_summary()` → paste-ready block.  Column
  classification is name-pattern based (`_is_metadata_col`, `_is_identifier_col`)
  so real headers like `5_NIST_A-1` count as samples and `SwissLipid_identifier`
  as metadata.  Zero LLM calls; agent just reads numbers.
- Tests: tests/test_maf.py (20: real names, regex, parsing count, filter,
  scoring, analysis named/mz-only/identifier-only, subdir layout, summary).
  Run: `.venv-local/bin/python -m pytest tests/ -q`.

## Installable skill (portable packaging)
- Standard: Agent Skills spec (agentskills.io) — `SKILL.md` + `frontmatter`
  (name, description, compatibility, metadata), optional `scripts/`,
  `references/`, relative-path references, SKILL.md < 500 lines.
- Harnesses honored by the installer: pi (`~/.pi/agent/skills`),
  Claude Code (`~/.claude/skills`), opencode (`~/.config/opencode/skills`,
  `~/.opencode/skills`), shared `~/.agents/skills`. Override via
  PI_SKILLS/CLAUDE_SKILLS/OPENCODE_SKILLS env.
- `scripts/install.sh`: creates `.venv-local` (uv preferred, falls back to
  python3 -m venv), pip-installs `-e .`, symlinks repo root into each harness
  skills dir, validates with `skills-ref` if present. Idempotent.
- `scripts/python`: venv-python wrapper (portable — no hardcoded paths).
- `scripts/uninstall.sh`: removes the skill symlinks (keeps the venv).
- SKILL.md now portable: setup points at `./scripts/install.sh` + `scripts/python`;
  deep API detail moved to `references/api.md`; never references a home dir.
- Verified: install+uninstall in a sandbox HOME; SKILL.md reachable through all
  harness symlinks (~168 lines < 500).

## Docs
- `docs/perf.md` — benchmark results + justification (keep-alive, process parse, overlap, listing cache, by-value fix; rejected options: HTTP/2, async, REST-zip primary).
- `docs/design-pipeline.md` — the typed-pipeline design document.
- `docs/index.html` — self-contained styled guide (no build/CDN, opens directly):
  problem → how it works → install → quick start → deterministic-vs-AI → API table
  → troubleshooting. Copy buttons on code blocks; pipeline diagrams.
- `docs/README.md` — points to index.html + sibling docs.

## Auto-generated API docs
- `scripts/gen_api_docs.py` — dependency-free (stdlib `inspect`) scraper over
  `metabo_search.__all__`: writes `docs/api.md` (full reference) and refreshes the
  compact API table in `docs/index.html`. Runs on `install.sh`; manually with
  `scripts/python scripts/gen_api_docs.py`.
- `docs/index.html` is now compact + includes an SVG workflow flowchart with the
  three loops (deterministic fetch-more, dataset feedback, wording revision).

## Flowchart
- `docs/index.html` workflow chart now uses **Mermaid v11** (ESM from jsdelivr CDN,
  dark theme matching the page) instead of hand-rolled SVG. Loops are explicit
  back-edges: fetch-more pages (SC→S), dataset feedback (Q→U), wording revision
  (A→L). Requires internet to render; rest of page is still self-contained.

## Docs refresh (guide)
- Problem statement: "The hunt for the right dataset ends here…" (high-level, 2 sentences).
- Chart: compact Mermaid TD, self-loop arcs for the 3 loops (fetch-more, wording revise;
  Q→U back-edge for dataset feedback). Fits on screen.
- Quick start section removed; replaced by 4 rough API examples (discovery / sentences /
  download / offline+manifest).
