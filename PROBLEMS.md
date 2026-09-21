# metabo-search — problems found by adversarial testing

> **Status: all items below are FIXED.** Regression tests live in
> `tests/test_pipeline_regressions.py`; the implementation plan (P1–P9, order,
> files touched) is in `docs/fix-plan-audit.md`. "0-duration TTL" was
> re-evaluated and deliberately NOT changed (it is the established
> always-stale idiom, exercised by existing tests); the species-map bug
> found while implementing P6 is documented under P6 and fixed in
> `repositories/workbench/corpora.py`. Live smoke runs after the fixes:
> discovery unchanged, sentences read "A Homo sapiens urine specimen."
> (was "A urine urine specimen."), harvest/max_files=0 refused at build.

Date: 2025-09-21. Method: read the code, then exercised the public surface
(`quick_discovery` / `find_datasets` / `harvest` / `describe` / `download` /
`screen` / custom predicates / cache replay) with edge inputs, offline
fixtures, and live API runs (both repositories). All 116 existing tests still
pass; **no code or tests were added to the repo.**

Reproducer scripts for every finding below were run from `/tmp` (not in the
repo). Severity: 🔴 breaks the documented flow / wrong data, 🟠 silent
misbehavior, 🟡 cosmetic / minor.

---

## 🔴 1. `download()` / `harvest()` "constraint" gate leaks → downloads EVERYTHING

The guard built to *refuse unconstrained downloads* tests `x is not None`
(`core/steps.py:479`), but the downloader treats empty containers and falsy
numbers as *no filter* (`downloader.py:277,283,285`: `if config.categories:`
/ `if config.max_files:` / `if config.max_size_gb:`).

Verified: with a 3-file listing (raw/derived/other),

| config | gate says | downloader does |
|---|---|---|
| `categories=[]` | constraint (n=1) | **keeps all 3** |
| `file_types=[]` / `sample_names=[]` | constraint | **keeps all 3** |
| `max_files=0` | constraint (0 is not None) | **keeps all 3** (0 is falsy → no cap) |
| `max_size_gb=0.0` | constraint | **keeps all 3** |
| `max_files=1` | constraint | keeps 1 (correct) |

So both entry points whose docs promise "never silently downloads everything"
are bypassable with `harvest(download_kwargs={"max_files": 0})`,
`harvest(download_kwargs={"categories": []})`, or
`pipeline(download(max_files=0), input=insp)` — all validate cleanly and then
fetch the study's full data tree. For a 1753-sample study at MB-scale files
this is a real foot-gun. Fix idea: require non-empty lists and `> 0` for
max_files/max_size_gb on both the harness and the body filter.

## 🔴 2. `run(input=...)` — documented midstream continuation is broken

`Pipeline.run(input=...)` folds from the run-time input but calls
`self.validate()` which only sees the **construction-time** `self.input`
(`core/steps.py:620`). So every documented midstream pattern fails:

- SKILL.md / AGENTS.md example `pipeline(score(profile)).run(input=r["inspect"])`
  → `ValueError: score() needs deep candidates — add inspect() before it`
- `pipeline(inspect(), score()).run(input=filter_result)` → `ValueError:
  inspect() needs a shallow carrier (search result or a pre-inspect filter)`

The only working variant is the undocumented construction form
`pipeline(score(), input=insp)`. Since `find_datasets` builds with
construction-time input, it is unaffected — but the skill's stated iteration
loop ("= slicing + extending: `r2 = pipeline(score(profile)).run(input=r['inspect'])`")
does not work.

## 🔴 3. Workbench studies produce ZERO sample sentences (silent)

`collect_sample_contexts` short-circuits on `not candidate.sample_file_parsed`
(`sample_gen.py:89`). Workbench `deep_metadata` never sets
`sample_file_parsed=True` (it sets `investigation_file_parsed=True` only,
`repositories/workbench/inspect.py:41`). Result: for any workbench candidate,

- `describe()`/`full_report()` yields **0 sentences** (verified: 1 LLM call is
  still made per study → wasted; `by_study[sid] == []`);
- the `SampleManifest` sentence column is empty for those rows;

and nothing warns. The workbench is a default database (`DEFAULT_DATABASES`),
so a workbench-only run silently returns an empty describe result. The seam
already exposes `WorkbenchRepository.sample_rows(candidate)` — it is simply
not wired into `collect_sample_contexts`. (Also: a top-4 mixed live run
happened to rank all-MetaboLights, which masks the issue by luck of ordering.)

## 🔴 4. `_get_char` substring match → organism = tissue ("urine urine specimen")

`_get_char(row, "Organism")` (`sample_gen.py:586`) matches any column whose
name *contains* the key, so `"Characteristics[Organism part]"` satisfies the
key `"Organism"`. Verified:

- row with both `Characteristics[Organism]` and `Characteristics[Organism part]`
  → organism = `"urine"` (the FIRST matching column in dict order wins);
- row with only `"Characteristics[Organism part]"` (very common in real ISA)
  → organism = `"urine"`, tissue = `"urine"`.

Visible in the live run: `MTBLS8662` → "A urine urine specimen.",
`MTBLS72` → "A blood plasma blood plasma specimen.". The flagship per-sample
sentence output has the tissue stuffed into the organism slot.

## 🟠 5. `download()` / `describe()` ignore the repository seam (mixed-repo pipelines)

`_do_download` (`core/steps.py:1008`) and `_do_describe` (`core/steps.py:979`)
do not dispatch on `candidate.repository`:

- `download()` on a workbench candidate builds
  `https://ftp.ebi.ac.uk/.../studies/public/{ST000001}/FILES/`
  (a MetaboLights URL for a workbench id) — verified; the result is a
  misleadingly "successful" `DownloadResult` with 0 files
  ("No data files found", no failure surfaced). The seam's
  `StudyRepository.download` exists for exactly this, but is bypassed.
- `describe()` on a workbench candidate costs an LLM call and returns 0
  sentences (see #3).

## 🟠 6. Offline / fixtures mode still hits the LIVE metstat API — and crashes without network

`METABO_WORKBENCH_FIXTURES` makes the corpus fully offline
(`repositories/workbench/corpora.py`), but the search fast path calls
`metstat_matches` → `client.metstat(...)` live whenever the profile produces
confident slots (`repositories/workbench/search.py:104,234`). Two failure
modes verified:

- Offline (no network): the whole search raises
  (`RuntimeError: cannot reach metabolomicsworkbench.org`) instead of falling
  back to corpus term-matching — the code's own docstring says the fast path
  "is never the sole path", but offline it is (and it raises).
- Fixtures + live metstat available (my first offline run): the live pool
  (`ST004524 …`) does not intersect the fixture corpus (`ST005xxx`), so the
  run silently returns **0 candidates** with `notice: None` — nothing tells
  the agent the pool came from a different corpus than the one searched.

## 🟠 7. Malformed LLM recipe output: raw crashes + junk cached forever

`submit_samples(task, llm_text)`:

- non-JSON → raw `JSONDecodeError`,
- JSON array → raw `AttributeError: 'list' object has no attribute 'get'`
  (`parse_study_profile`, `sample_gen.py:246`). No guard, no hint about what
  to fix; a bad LLM reply mid-`full_report().run(llm=...)` aborts the run with
  an internal stack trace and no guidance.
- Missing `sentence_template` key → silently accepted, template `""`, and the
  garbage ("." sentences) is **written to the eternal revision-keyed cache**
  (verified: `load_samples(task)` returns the junk on every later run).

Given the skill's explicit instruction that the agent authors this JSON, a
lenient parse + explicit error message + refusing to cache empty templates
would close the loop.

## 🟠 8. Zero / negative limits silently destroy results

| Call | Behavior (verified) |
|---|---|
| `screen(profile, min_survivors=0)` | passes validation; `survivors[:0]` → **all studies dropped**, downstream inspect/score run on [] — no warning (steps.py:877 workbench: corpus empty) |
| `screen(..., min_survivors=-1)` | `[: -1]` keeps all but the last |
| `describe(top=-1)` | `ranked[:-1]` → silently skips the last candidate |
| `maf(min_metabolites=0)` | requires `metabolite_count >= 0` → trivially true (redundant, harmless) |
| `parse_ttl("0d"/"00d")` | parses to 0 s → every cached entry is *permanently stale* (the opposite of "cache me") |

None of these are validated at plan time. `describe(top=0)` additionally
raises the "needs the LLM" error even though it would describe nothing.

## 🟡 9. Minor API/consistency notes

- `quick_probe(...)` has no `cache_root=` kwarg while `quick_discovery(...)`
  does (`core/recipes.py:22,33`); docs route around it with `.cache(...)`, but
  the asymmetry is easy to hit.
- `load_study_from_isa` on a directory containing only `i_Investigation.txt`
  marks the study `inspection_depth="deep"` (property keys off
  `investigation_file_parsed`) despite zero sample/assay/MAF data — a study
  with essentially no metadata is presented as fully inspected.
- `_do_describe` requires `llm` even when `top=0` / zero candidates.
- `FilterResult.digest()` is snapshot-sensitive (mutates → digest changes);
  the fold deep-copies at boundaries so this is safe only if every caller
  follows the convention — worth a comment or defensive copy in `digest`.
- `SampleSentencesStore` with a store file that is valid JSON but not a dict
  (e.g. `[...]`) → `AttributeError` at first `.get` (only corrupt *JSON* and
  OSError are handled).
- `harvest()` prompts for a download constraint but after the #1 fix its
  `categories`/`max_files`/`max_size_gb` checks need the same `> 0` / non-empty
  semantics.

## What survived (no issues found)

- Fresh↔warm↔force cache byte-identity (verified live, both repos, plan
  audit: search 7d / inspect 30d / describe eternal).
- Per-db cache keying when `databases` changes.
- Pydantic-free dataclass serialization round-trips (unicode, nested maps,
  dicts, None).
- `pipeline(...).extend(...)` warm-prefix continuation.
- Validation diagnostics for most structural mistakes (search mid-chain,
  score-without-inspect, wrong-stage predicates, unconstrained download in
  the `None` case) — good messages.
- Repository merge order and per-repo screening in mixed live runs.
- LLM outputs *are* never cached on an exception (only the empty-template case
  in #7 leaks junk into the cache).