# Fix plan — bugs pinned by `tests/test_pipeline_regressions.py`

> **Status: IMPLEMENTED.** All P1–P9 fixes are in; the full suite is green
> (116 pre-existing + 33 regression = 149) and the live smoke run passes.
> Two deviations from the original plan, both intentional:
> - **P8 `parse_ttl("0d")` NOT changed** — a zero TTL is the codebase's
>   established always-stale idiom, deliberately exercised by
>   `test_cache_ttl_expiry` / `test_cache_store_lookup_ttl`; blocking it would
>   break working behavior for a non-bug. The regression test asserting a
>   raise was dropped (noted in place).
> - **P6 uncovered a real species-map bug**: `build_corpus` keyed
>   `species_common` by the REST response's row index, so every organism
>   screen joined against `summaries` on disjoint id spaces (live: 0/1891
>   intersect) and silently dropped everything. Fixed in
>   `repositories/workbench/corpora.py` and covered by the P6 regression
>   tests.

The regression suite (`tests/test_pipeline_regressions.py`) asserts the desired
behavior for every problem in `PROBLEMS.md`. Originally 30 of 34 tests failed
on purpose (they pin the bugs); each fix below flipped its tests green.

---

## P1 — download()/harvest() constraint gate leaks → downloads EVERYTHING

**Files:** `core/steps.py` (validation), `core/recipes.py` (harvest guard),
`downloader.py` (`_apply_filters`, defense-in-depth).

**Root cause:** the gate counts `x is not None` as "a constraint", but the
downloader's `if config.categories:` / `if config.max_files:` treat empty
lists and falsy numbers as *no filter*. So `categories=[]`, `file_types=[]`,
`sample_names=[]`, `max_files=0`, `max_size_gb=0.0` all validate as
constrained and then fetch the whole study.

**Fix:**
1. `core/steps.py` `_errors` download branch: a real constraint is
   `v is not None and (v != [] if list else v > 0 if number else True)` —
   i.e. reject empty buckets and `<= 0` caps. Update the error message to
   say "non-empty categories/file_types/sample_names; max_files/max_size_gb > 0".
2. `core/recipes.py` `harvest`: same predicate on the kwargs (the current
   `any(k in dl for k in ...)` check passes `max_files=0`/`categories=[]`).
3. Defense in depth in `downloader._apply_filters`: make `0` mean
   *download nothing* (`selected[:0]` → `[]`, `size <= 0` → nothing) so even
   direct legacy `download_data_files(c, DownloadConfig(max_files=0))` calls
   can never fetch everything.

**Tests flipped green:** `test_download_empty_or_zero_constraints_rejected*`,
`test_harvest_rejects_empty_or_zero_constraints*`.

## P2 — `run(input=...)` midstream continuation is broken

**File:** `core/steps.py` (`Pipeline.run`, `Pipeline._errors`).

**Root cause:** `run(input=X)` folds from the run-time input but calls
`self.validate()`, which only sees the **construction-time** `self.input`, so
the documented `pipeline(score()).run(input=r["inspect"])` /
`pipeline(inspect(), score()).run(input=filter_result)` fail validation.

**Fix:** thread the effective input through validation — e.g. refactor
`_errors(input_: Result | None = None)` and have `run()` call
`self.validate(input=init)`. Keep behavior identical when no run-time input
is passed (construction-time pipelines, `find_datasets`).

**Tests flipped green:** `test_run_midstream_score_with_runtime_input`,
`test_run_midstream_inspect_with_runtime_input`.

## P3 — workbench studies produce zero sample sentences (silent)

**Files:** `repositories/workbench/inspect.py`, (optionally)
`sample_gen.collect_sample_contexts`.

**Root cause:** `collect_sample_contexts` bails when
`sample_file_parsed` is False; workbench `_fetch_study` never sets it (it
sets `investigation_file_parsed` only). So the ship's factor rows — which it
DOES have — are invisible to describe/manifest.

**Fix:**
1. In workbench `_fetch_study`, set `c.sample_file_parsed = True` when
   `sample_metadata` is non-empty (the "sample sheet known" flag; workbench
   has no ISA files, but it has sample rows).
2. Follow-up (not needed for green, but makes the feature useful): expand the
   workbench combined `factors` column ("Genotype:… | Treatment:…") into
   `SampleContext.factors` / `characteristics` so factor + code slots resolve
   for workbench studies (wire `WorkbenchRepository.sample_rows` or parse in
   `_fetch_study`). Without this, sentences exist but slots stay empty.

**Tests flipped green:** `test_workbench_deep_candidate_yields_sample_contexts`,
`test_workbench_describe_produces_sentences`.

## P4 — `_get_char` substring match: organism == tissue ("urine urine")

**File:** `sample_gen.py` (`_get_char`).

**Root cause:** `if key in col` matches `"Organism"` inside
`"Characteristics[Organism part]"`, and the first matching column wins —
order-dependent, wrong values.

**Fix:** require the full bracketed label — match
`col == f"Characteristics[{key}]"` (or `startswith(f"Characteristics[{key}]")`);
`"Organism"` can then never match `"Organism part"`. Optionally move the
label extraction to a single regex so both organism and tissue read the same
way.

**Tests flipped green:** `test_get_char_organism_not_matched_by_organism_part*`
(both orders), `test_get_char_organism_part_only_leaves_organism_empty`,
`test_contexts_organism_and_tissue_are_distinct`.

## P5 — download()/describe() ignore the repository seam

**Files:** `core/steps.py` (`_do_download`, `_do_describe`).

**Root cause:** `_do_download` feeds every candidate to the MetaboLights
downloader (building `ftp.ebi.ac.uk/.../{ST000001}/FILES/` for workbench ids)
and `_do_describe` calls the LLM per study even when there is nothing to
describe.

**Fix:**
1. `_do_download`: group `insp.candidates` by `candidate.repository`; dispatch
   MetaboLights to the existing body; any repository whose
   `StudyRepository.download` raises `NotImplementedError` → let that loud
   error propagate (list the unsupported repos/ids in the message). A mixed
   result containing workbench studies therefore fails loudly instead of
   silently "downloading 0 files".
2. `_do_describe`: skip candidates with no contexts (via
   `collect_sample_contexts(...)`); only call the LLM for studies that will
   actually produce sentences.

**Tests flipped green:** `test_download_workbench_candidate_fails_loudly`,
`test_describe_skips_llm_for_study_without_contexts`.

## P6 — offline/fixtures mode hits the live metstat API (and crashes offline)

**Files:** `repositories/workbench/search.py` (`search_workbench` /
`metstat_matches`), `repositories/workbench/client.py`.

**Root cause:** the fast path calls `client.metstat(slots)` unconditionally;
in fixture mode (offline tests) that's a live call, and with no network it
raises instead of using the corpus backstop the design promises.

**Fix:**
1. Treat metstat as *best-effort*: wrap the call; on any exception (or when
   running under `METABO_WORKBENCH_FIXTURES`), fall back to the corpus-only
   path with `meta["fast_path"] = False` and a `notice` explaining slots were
   requested but the pool was unavailable.
2. When the pool comes back but intersects the corpus in **zero** studies
   (stale/partial corpus), don't silently return `[]` — fall back to
   corpus-wide ranking with a notice (e.g. "metstat pool had no overlap with
   the cached corpus; ranked corpus-wide").

**Tests flipped green:** `test_workbench_search_offline_when_metstat_unavailable`,
`test_workbench_empty_metstat_pool_falls_back_to_corpus`.

## P7 — malformed LLM recipe: raw crashes + junk cached forever

**File:** `sample_gen.py` (`parse_study_profile`, `submit_samples`).

**Root cause:** `parse_study_profile` does an unguarded `json.loads` +
`.get(...)` (raw `JSONDecodeError`/`AttributeError` on non-dict JSON) and
silently accepts an empty `sentence_template`, whose garbage ("." sentences)
is then written to the eternal revision-keyed cache.

**Fix:**
1. Wrap the parse: raise `ValueError` with an actionable message (must be a
   JSON object; fix the LLM output; no code fences) on non-JSON, non-object,
   and missing/non-string `sentence_template` (message names the key).
2. Ensure `submit_samples` never caches before the full apply succeeds
   (already true on exception — keep it, add a guard so an empty template can
   never reach `store.save`).

**Tests flipped green:** `test_submit_samples_non_json_raises_clear_error`,
`test_submit_samples_array_json_raises`,
`test_submit_samples_empty_template_rejected_and_not_cached`
(+ the two already-passing cache tests stay green).

## P8 — zero/negative limits silently destroy results

**File:** `core/steps.py` (`_errors`), `core/cache.py` (`parse_ttl`).

**Fix (plan-time validation, loud not silent):**
- `screen/min_survivors` < 1 → `ValueError` ("keep at least 1 survivor").
- `describe(top)` < 0 → `ValueError`.
- (Bonus, same class) `inspect(workers)` < 1, `maf(min_metabolites)` < 1.
- `parse_ttl("0d")`: zero-duration is a mistake → `ValueError` (a TTL of 0
  makes every cached entry permanently stale, the opposite of intent).

**Tests flipped green:** `test_screen_zero_or_negative_min_survivors_rejected*`,
`test_describe_negative_top_rejected`, `test_parse_ttl_zero_duration_rejected`.

## P9 — minors

1. `core/recipes.py` `quick_probe`: add `cache_root=None` kwarg mirroring
   `quick_discovery` (`.cache(cache_root)` when given).
2. `inspector.load_study_from_isa` / `StudyCandidate.inspection_depth`:
   require evidence beyond the investigation file — deep ⇔
   `investigation_file_parsed AND (sample_file_parsed OR assay_files_parsed)`.
3. `_do_describe`: when `cfg.top <= 0` or `ranked` is empty, return an empty
   `DescribeResult` without demanding an LLM.

**Tests flipped green:** `test_quick_probe_accepts_cache_root`,
`test_isa_with_only_investigation_file_is_shallow`,
`test_describe_top_zero_without_llm_ok`.

---

## Suggested order & effort

| # | Fix | Size | Ties out |
|---|-----|------|----------|
| P4 | `_get_char` label boundary | XS | 4 tests |
| P8 | plan-time validation + ttl("0d") | XS | 4 tests |
| P7 | recipe parse guard | S | 3 tests |
| P2 | validate(run-time input) | S | 2 tests |
| P1 | constraint predicate (steps+recipes) + `_apply_filters` 0-means-none | S | 10 tests |
| P6 | metstat best-effort + pool-miss fallback | M | 2 tests |
| P3 | workbench `sample_file_parsed` (+ factor-column follow-up) | M | 2 tests |
| P5 | seam dispatch for download/describe | M | 2 tests |
| P9 | quick_probe kwarg, ISA depth, top=0 no-llm | S | 3 tests |

## Verification (after all fixes)

1. `.venv-local/bin/python -m pytest tests/ -q` → **150** green
   (116 existing + 34 regression).
2. Live smoke (network is available in this workspace):
   - `quick_discovery("alzheimer", profile..., min_survivors=6).cache(root).run()`
     → same ranked list as before, workbench pool no longer empty-on-fallback;
   - `full_report(...).run(llm=...)` on a top MetaboLights study → sentences
     no longer show "urine urine specimen" (organism/tissue correct);
   - `harvest(download_kwargs={"max_files": 0}, ...)` → raised at build time.
3. Docs to touch after the fixes: `SKILL.md` "Errors & recovery" (download
   constraint semantics), `PROBLEMS.md` (mark each item resolved), and the
   `core/steps` docstrings for the new validation messages.