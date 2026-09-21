# Implementation checklist — Metabolomics Workbench repository

Milestones from `docs/plan-metabolomics-workbench.md`. Each item ships green
(tests pass) and with docstring-documented contracts.

## A. Foundation & seam
- [x] M1: rename `mtbls_agent` → `metabo_search` (done upstream; stale dist-info pending)
- [x] `StudyCandidate.repository` field (+ serialization/digest-compat)
- [x] `StudyRequirements.diseases: list[str]` field
- [x] `repositories/base.py`: `StudyRepository` ABC, `DISPATCH`, `database_from_id`, `validate_databases`, `DEFAULT_DATABASES`
- [x] `repositories/metabolights` adapter (delegates to existing top-level modules)
- [x] `SearchConfig.databases` + `search()` factory + recipes/`find_datasets` forwarding
- [x] `_do_search` per-db dispatch, candidate tagging, ordered merge
- [x] per-db search caching (no cross-invalidation on `databases` change; respects step TTL + `force`)
- [x] `_do_inspect` dispatch by `repository` (position-preserving contract)

## B. Workbench search (offline-first)
- [x] `workbench/client.py` — pooled httpx wrapper + endpoint builders (180 s corpus timeout)
- [x] `workbench/corpora.py` — whole-corpus + disease/source/species maps, 7 d disk TTL, 3× retry
- [x] `workbench/vocab.py` — vendored vocab snapshots (259/328 dist. values; 486 latin/450 common) + derivation
- [x] `workbench/matcher.py` — rapidfuzz engine, alias table, cutoff 75 + tie-slap 2 gate (MatchResult)
- [x] `workbench/slots.py` — metstat slot assembly; structured fields only; confident fills; free text never a slot
- [x] `workbench/search.py` — metstat pool (ranked by text, never starved) + corpus term-match backstop
- [x] ambiguity notice: empty slot + actionable recipe text (incl. `databases=` + `input=` steps) in `args_used`/`fmt`
- [x] shallow field mapping for screening: organism_parts from source map; analysis_type into descriptors

## C. Workbench deep inspect & MAF
- [x] `workbench/inspect.py` — parallel (threads) summary/factors/analysis/metabolites → deep candidate, soft-fail
- [ ] `workbench/datatable.py` — datatable → MAF-shape (fullest-column count, annotation levels) [stub returns []]
- [ ] `filter_by_maf` / `maf()` adapter for workbench candidates
- [x] `sample_rows()` for describe/manifest (factors-based)

## D. Tests (all offline, pinned counts)
- [x] `test_database_option.py` — default both; `("metabolights",)` unchanged; bad name ValueError; merge order + tag; per-db cache reuse
- [x] `test_workbench_matcher.py` — real-phrase→real-list; alias bridges; tie/off-target abstain
- [x] `test_workbench_vocab.py` — pinned counts; aliases resolve; latin→common
- [x] `test_workbench_search.py` — corpus determinism; fast-path pool semantics; notice actionable; min_samples
- [ ] `test_workbench_cache.py` — (covered in test_database_option.py) — fold per-db ttl/force tests here
- [ ] `test_workbench_ambiguity.py` — (notice covered in search tests) — pipeline-level notice in fmt
- [ ] `test_core_workbench_pipeline.py` — end-to-end fixtures, fresh==warm, mixed repos

Status: **164 tests green**; live smoke verified
against the real API (corpus 96 s first fetch, then cached; metstat pool 30;
Human/Blood/Diabetes slots; inspect deep with ion modes; scored table).

## E. Docs & skill
- [ ] SKILL.md / references/api.md copy-paste "ambiguous workbench match" recipe
- [ ] update AGENTS.md current-status + docs/perf.md workbench section

## Later (M5+ / v2)
- [ ] workbench `download` (datatable + mwtab) · describe/manifest via factors
- [ ] full migrate: physically move top-level ML modules under `repositories/metabolights/`
- [ ] metstat ANALYSIS/POLARITY/CHROM probe milestone
- [ ] embed/vector ranking of study summaries (Phase 3)