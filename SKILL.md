---
name: metabo-search
description: Find and score metabolomics datasets against experimental requirements (organism, tissue, technique, disease, sample count, MAF availability) via a typed, cacheable pipeline over MetaboLights AND Metabolomics Workbench — deterministic discovery, warm-cache iteration, per-sample biological sentences, selective downloads, manifest export. Use when a researcher needs datasets matching their experimental setup.
compatibility: Python 3.11+; uv optional (falls back to python3 -m venv)
metadata:
  install: scripts/install.sh
  package: metabo_search
---

# metabo-search

Find datasets, pick the fit, then generate per-sample biological sentences.
**One import surface**: everything lives at `from metabo_search import ...`.

## Setup (run once, from the skill root)

```bash
./scripts/install.sh       # creates .venv-local, pip-installs the package,
                           # links the skill into pi/claude/opencode skill dirs
scripts/python -c "import metabo_search; print(metabo_search.__file__)"
```

> Never use system/homebrew python (broken pyexpat on macOS). If the import
> resolves to a weird path, re-run `./scripts/install.sh` (private venv
> `.venv-local` → THIS src). Uninstall: `scripts/uninstall.sh`.

Full API reference: [references/api.md](references/api.md) ·
run-tested examples: [docs/cookbook.md](docs/cookbook.md) ·
design: `docs/design-pipeline.md`.

---

## The One Flow (copy-paste)

```python
from metabo_search import (pipeline, search, filter, screen, inspect, score,
    describe, RequirementProfile, StudyRequirements,
    quick_probe, quick_discovery, full_report)

profile = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"],
                           diseases=["Alzheimer's disease"],
                           has_maf=True),
    nice_to_have=StudyRequirements(techniques=["LC-MS"]),
)

# discovery = search → screen → inspect → score (no LLM).  cache root makes
# re-runs replay warm prefix steps (search 7d / inspect 30d TTLs).
p = quick_discovery("urine alzheimer", profile).cache(".pipeline_cache")
print(p)                      # step ladder — print(p), NOT p.ladder()
r = p.run()
print(r["score"].fmt())       # ranked, one line per study

# extend to per-sample sentences (ONE LLM call/study, cached per revision):
r2 = p.extend(describe(top=3)).run(llm=call_llm)
print(r2["describe"].fmt(detail=True))
```

> `call_llm(prompt)` = **you** generating text with your own model. The library
> never calls an LLM; only `describe` reads it, via `run(llm=...)`.

**Results are plain dataclasses you can construct by hand** to continue a run
midstream (`pipeline(...).run(input=<any result>)`) — exact fields in
references/api.md, patterns in docs/cookbook.md. `print(p)` is the ladder;
there is no `p.ladder()` method.

### Steps (each Config → Result; empty config = do nothing)

| Step | Input → Output | Notes |
|---|---|---|
| `search(query, profile=…, databases=…)` | → `SearchResult` | `query` is the one required answer; `databases` defaults to ALL repositories, candidates tagged `repository=` |
| `filter(screen(profile, min_survivors))` | SearchResult → `FilterResult` | drop hard-fails on search-index data; rank survivors; no truncation by default |
| `filter(maf(require, min_metabolites))` | InspectResult → `FilterResult` | drop MAF-poor studies (deep; place after inspect) |
| `filter(custom(name, **params))` | any → `FilterResult` | the extension point (registry) |
| `inspect(workers=10)` | → `InspectResult` | deep ISA download + parse; cache root keeps ISA dirs |
| `score(profile=…)` | → `ScoreResult` | ranked + comparison table; neutral pass-through without profile |
| `describe(top=3, revision=0)` | ScoreResult → `DescribeResult` | per-sample sentences; 1 LLM call/study, store-cached per revision |
| `download(categories=…, dest_dir=".")` | InspectResult → `DownloadResult` | needs ≥1 constraint (refuses to fetch everything) |
| `export(path="manifest.csv")` | InspectResult → `ExportResult` | flat CSV + traceability |

Steps accept `cache=CacheOpts(enabled, ttl)` and `print=PrintOpts(top, detail)`.

### When requirements are repo-specific (slice between stages)

One `screen(profile)` applies to every repository's candidates — incommensurable
requirements (serum/plasma, MAF, …) need per-repo slicing. How: filter
survivors by `c.repository`, hand-build a `FilterResult`, continue with
`run(input=…)` (full worked pattern incl. Workbench deep serum check: cookbook).

```python
from metabo_search import FilterResult, inspect, pipeline, score

probe = quick_probe("serum", profile).cache(".metabo_cache")
f = probe.run()["filter"]                       # FilterResult(survivors, dropped,
                                                #   order, stage)

# ML exposes serum/plasma as a SEARCH FACET — decide at the shallow stage:
serum_plasma = [c for c in f.survivors
                if c.repository == "metabolights" and any(
                    s in t.term.lower() for t in c.organism_parts
                    for s in ("serum", "plasma"))]

deep = pipeline(inspect(workers=10)).run(
    input=FilterResult(survivors=serum_plasma, stage="shallow"))
r2 = pipeline(score(profile)).run(input=FilterResult(
    survivors=deep.candidates, stage="deep"))   # stage="deep" = post-inspect
```

---

## Repositories: which databases to search

Default `databases=("metabolights", "metabolomics_workbench")`. Rules:
- **`databases=("metabolights",)`** reproduces pre-workbench behavior exactly.
- The **workbench needs a cache root** (`.cache("…")` — its whole index is
  fetched once/7 d then filtered locally). Without a root, restrict
  `databases`.
- **Vocabulary ambiguity is an abstention, not a guess.** When a workbench
  slot can't be matched confidently it stays EMPTY and results print an
  actionable notice (top candidates + counts + the exact next call): refine
  free text / `profile.hard.diseases` and re-run with
  `databases=("metabolomics_workbench",)`; MetaboLights results are untouched
  (per-repo cache).
- `StudyRequirements.diseases` is the structured disease channel; free text
  is matched against titles client-side.
- **`has_maf=True` (or `min_metabolites`) in a mixed profile silently drops
  the whole Workbench side** — it never ships `m_*.tsv` MAF files. Either
  restrict `databases=("metabolights",)` or put MAF in `nice_to_have`.

### Facets & vocabularies (read before writing a profile)

Each repo has its OWN controlled values; the shared screen matches them
literally (substring, case-insensitive). Wrong vocabulary = silent
under-recall:
- **MetaboLights `sample_types` are the facet values `organismParts.term` —
  case and wording matter.** Canonical biofluids: `"blood plasma"` (≈202 human
  studies), `"blood serum"` (≈128); `"Serum"`/`"Plasma"` are residual variants
  (~19/5). Facet on the canonicals (often with a one-word query) and push
  disease/condition filtering client-side (title/abstract) — facets don't
  carry it.
- **Workbench `organisms` are the Latin names** (`"Homo sapiens"`, not
  `"Human"` — `"Human"` silently drops every candidate). One shared profile
  serves both repos.
- **Workbench SOURCE has no serum/plasma** — canonicals: `"Blood"` (plus
  variants like `Seminal plasma`). Serum-vs-plasma must be **verified per
  sample after inspect**: ML → `row["Characteristics[Organism part]"]`,
  WB → `row["sample_source"]` (e.g. `"Blood (plasma)"`).
- **Workbench disease terms are title-cased canonicals** (`Cancer`, `Lung
  cancer`, `Alzheimers disease`); the matcher prefers an exact value over a
  fuzzy subtype — use the exact canonical (`"cancer"` → `"Cancer"`, never
  `"Lung cancer"`).

## Staged work (cheap first, deep later)

```python
probe = quick_probe("urine alzheimer", profile).cache(".pipeline_cache")
for tightened in profiles:              # stage 1: narrow the profile,
    print(probe.run()["filter"].fmt())  #   no inspect, warm replay

deep = probe.extend(inspect(), score(profile))   # stage 2: only when settled
r = deep.run()
r2 = pipeline(score(profile)).run(input=r["inspect"])  # continue midstream
```

## Annexes (nothing is implicit) + single-call shortcuts

```python
from metabo_search import maf, download, export

p4 = (p
      .extend(filter(maf(min_metabolites=200)))
      .extend(describe(top=3))
      .extend(download(categories=["raw"], dest_dir="./data"))  # needs a constraint
      .extend(export(path="manifest.csv")))
r4 = p4.run(llm=call_llm)

report = quick_discovery("urine alzheimer", profile).run()  # == find_datasets(...)
done   = full_report("urine alzheimer", profile, top=3).run(llm=call_llm)
```

---

## API table (legacy function surface — still works; the pipeline wraps these)

| Function | Returns | What it does |
|---|---|---|
| `find_datasets(query, profile, databases=...)` | `ComparisonReport` | deterministic discovery: per-repo search → shallow screen → deep-inspect survivors → score → rank |
| `screen_candidates(shallow, profile)` | `ScreeningResult` | deterministic shallow hard-pass + rank (drop failures before slow deep inspect) |
| `profile_to_search_args(profile)` | `dict` | maps hard reqs → search API filters (server-side) |
| `filter_by_maf(deep, require_maf=..., min_metabolites=...)` | `[StudyCandidate]` | post-inspection: require/exclude MAF studies |
| `download_maf_files(study_id, dest)` | `[Path]` | fetch just the `m_*.tsv` files |
| `analyze_maf_files(id, isa_dir\|maf_paths)` | `[MafAnalysis]` | no-LLM MAF analysis: #metabolites, #samples, names vs identifiers vs m/z-only |
| `render_maf_summary(analyses)` | `str` | paste-ready text block |
| `prepare_samples(deep_study, store)` | `SampleTask` | bundles contexts + the ONE profile prompt |
| `load_samples(task)` / `submit_samples(task, llm_text)` | `[SampleDescription] \| None` / `[...]` | cached sentences / apply your recipe + cache |
| `list_data_files(deep_study)` | `[DataFileRef]` | recursive `FILES/` listing w/ sizes |
| `format_summary(deep_study)` | `dict[fmt→count]` | mzML/RAW/.d probe (+ dir-aware raw/derived split) |
| `download_data_files(deep_study, DownloadConfig)` | `DownloadResult` | selective download (bg: `start_download`) |
| `SampleManifest.build(deep, ...)` | `SampleManifest` | flat CSV + traceability |
| `search_studies / inspect_studies / score_studies / build_comparison_table` | — | the pieces, if you want control |
| `load_study_from_isa(id, dir)` | `StudyCandidate` | offline: rebuild from local ISA files |

You construct: `RequirementProfile`, `StudyRequirements`, `DownloadConfig`.

---

## MetaboLights files vocabulary (read once, saves probing)

Each study ships ISA-Tab metadata + data on
`https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/{MTBLS}/`:

| File | What it is |
|---|---|
| `i_Investigation.txt` | title/abstract, organisms, publications, protocols |
| `s_*.txt` | sample file — one row per sample (names, characteristics, factors) |
| `a_*.txt` | assay file — sample → data-file links, instrument, ion mode |
| `m_*.tsv` | **MAF** — one row per identified metabolite: name, formula, m/z, RT, database, per-sample abundance |
| `FILES/` | the actual data (raw/derived) — listed recursively by `list_data_files` |

`candidate.metabolite_count` / `maf_files_parsed` tell you a MAF was parsed;
`filter_by_maf(...)` / `download_maf_files(...)` filter/fetch them. For counts
+ a `named | identified | mz_only` verdict with zero LLM calls, call
`analyze_maf_files(id, isa_dir=...)` and paste `render_maf_summary(...)`.
**After any cached run the ISA dirs (incl. MAFs) are already local**:
`analyze_maf_files(id, isa_dir=r["inspect"].isa_dirs[id])` — or rebuild the
deep candidate with `load_study_from_isa(id, same_dir)` — costs nothing, no
`download_maf_files` needed. Metadata never exposes MS level — confirm from
a downloaded file or the paper.

> ⚠️ MAF is MetaboLights-only — `has_maf=True` in a mixed profile silently
> kills Workbench candidates (see Repositories).

## Deterministic vs AI — what needs the model

Discovery is fully deterministic once `RequirementProfile` is structured:
server-side filter → shallow screen → deep-inspect survivors → score → rank
(legacy `report.screening` carries survivors + dropped-reasons so you can
explain *why* studies failed). The model is only needed for (1) prose →
`RequirementProfile`, and (2) the per-study sentence recipe (1 cached call).

## Sentence wording: what's in, what's out

- **Include**: organism, tissue, disease/condition, biological state (fasted,
  baseline, post-challenge…), biologically relevant factors (sex, age, group).
- **Exclude**: subject/participant IDs, barcodes, plate/well positions, bare
  indices ("time point 1", "day 1", "replicate 3").
- **Rewrite** indices to meaning when the abstract supports it ("day 1 of
  fasting" → "after one day of fasting"); otherwise drop them.

## Wording feedback loop (until the user is happy)

```python
task  = prepare_samples(deep_study, store, revision=1)   # bump on each re-roll
descs = load_samples(task)
if descs is None:
    descs = submit_samples(task, call_llm(task.profile_prompt))
```
Each `revision` gets its own cache slot (old wording preserved for comparison);
`revise_samples(task, new_text)` auto-bumps the revision. Codes in **factor
values** (OGTT/OLTT/PAT/SLD) are decoded too: the recipe's `code` slot accepts
`field = <factor label>` (or `"data_files"`).

## What to decide (be fast, don't explore)

- **Discover** → `find_datasets` / `quick_discovery` with query + profile; judge top 2–3 by title/abstract/score.
- **Shallow leftovers** → `r["inspect"].fmt()` shows depth per study; retry stragglers serially before trusting sample/metabolite numbers (cookbook Pattern 6).
- **RC/blank samples** → auto-tagged "quality control"; don't treat them as patients.
- **disease=unresolved** → check `desc.used_sources`; re-prompt the profile with file-name codes or fix linkage. Don't accept silently.
- **Large studies** → always use a `SampleSentencesStore` (one LLM call, forever free).
- **Reuse metadata** → `load_study_from_isa(id, r["inspect"].isa_dirs[id])` — every cached run already keeps the ISA files on disk.
- **User error / API edge case** → note it and move on; don't loop.

## Errors & recovery
- **Datasets ≥8 with process parsing** → wrap the script in `if __name__ == "__main__":` (spawn on macOS re-imports the entry script into parse workers); in a throwaway script `MTBLS_PARSE_PROCESSES=0` forces thread parsing.
- `import metabo_search` resolves elsewhere → this repo shares a workspace with a parallel-test copy; use the private venv (`.venv-local`) so you import THIS src.
- **Deep inspect soft-fails by design**: a study that can't be fetched stays **shallow** instead of crashing (`c.inspection_depth != "deep"`; depth column in `fmt()`). Server overload (too many workers) is a common cause — keep `inspect(workers=8)` per repo; 16+ measured-refusals → shallow candidates. Retry stragglers serial (`workers=1`) with backoff; recovery loop in cookbook Pattern 6.
- ⚠️ **A degraded inspect is never cached**: if any candidate stayed shallow, the pipeline logs a warning and SKIPS the store — re-runs re-inspect instead of replaying stale shallow data. `run(force=True)` remains the bypass for any other stale cache entry; the retry loop (cookbook Pattern 6) is still how you avoid paying for the re-run.
- `.venv` broken → `rm -rf .venv && uv venv && uv pip install -e .`

## Limits to be honest about
- **Free-text queries are phrase-based**: "urine alzheimer" often returns 0
  while "alzheimer" finds studies. Prefer one strong term; push the rest into
  the profile's hard filters.
- `min_samples` / `min_raw_files` are **client-side post-filters** over at
  most `max_results` fetched hits — raise `max_results` when heavy filters
  bite; the returned set needn't be the "best" match set.
- The search index does NOT expose file formats or MS level. `format_summary()`
  confirms mzML/RAW presence from filenames, but **MS1 vs MS2 needs a
  downloaded file or the paper** — say so rather than guessing.
- **Workbench `metabolite_count` is a proxy, not a matrix dimension**: it's
  the `/metabolites` identified-list length, with no per-sample abundance
  columns (mwTab datatables aren't parsed yet). For a true m×s matrix
  requirement use MetaboLights MAF numbers (`analyze_maf_files`); quote
  workbench numbers as "ident. metabolite list length". The endpoint
  **omits the list for the largest studies** (`metabolite_list_unavailable`
  = True) — treat it as unknown, not 0; `min_metabolites` therefore does not
  hard-fail those (`criterion_explanations` says "UNAVAILABLE").
- Transient disconnects / one-match metstat pools are handled: the client
  retries and degrades a failed slot pool to corpus matching with a notice —
  the pool is an optimization, never a hard filter.
- **Parallelism is server-bound**: `inspect(workers≈8)` is the sweet spot per
  repo; 16+ overloads the remote file server → refusals → shallow candidates
  (see Errors & recovery for detection/retry/`force`).