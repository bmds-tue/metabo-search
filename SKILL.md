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
```

Use the venv python wrapper (portable, no hardcoded paths):

```bash
scripts/python -c "import metabo_search; print(metabo_search.__file__)"
```

> Never use the system/homebrew python (broken pyexpat on macOS). If
> `import metabo_search` resolves elsewhere, re-run `./scripts/install.sh`
> which re-points `.venv-local` at this repo. Uninstall: `scripts/uninstall.sh`.

Full API reference: see [references/api.md](references/api.md).

Design doc: see `docs/design-pipeline.md` (typed pipeline over the function
library). The legacy functions below still work — the pipeline is a thin,
cached wrapper over exactly those bodies.

---

## The One Flow (copy-paste)

```python
from metabo_search import (pipeline, search, filter, screen, inspect, score,
    describe, RequirementProfile, StudyRequirements,
    quick_probe, quick_discovery, full_report)

# 0) What the researcher needs:
profile = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"],
                           diseases=["Alzheimer's disease"],  # structured disease
                           has_maf=True),
    nice_to_have=StudyRequirements(techniques=["LC-MS"]),
)

# 1) Discovery — a typed pipeline; each step is Config → Result.
#    quick_discovery = search → screen → inspect → score (no LLM needed).
p = quick_discovery("urine alzheimer", profile).cache(".pipeline_cache")
print(p)                      # ladder: search('urine alzheimer') → filter(screen) → inspect → score
r = p.run()
print(r["score"].fmt())       # ranked, one line per study

# 2) Iterate cheaply: reformulate the profile / tighten the screen, then
#    re-run — warm prefix steps replay from the cache, only the suffix
#    executes.  Continue = slicing + extending:
p2 = p.extend(describe(top=3))
r2 = p2.run(llm=call_llm)     # per-sample sentences (ONE LLM call/study, cached)
print(r2["describe"].fmt(detail=True))
```

> `call_llm(prompt)` = **you** producing text with your own model. The library
> never calls an LLM; only `describe` reads it, via `run(llm=...)`.

### The steps (each is Config → Result; empty config = do nothing)

| Step | Input → Output | Defaults / notes |
|---|---|---|
| `search(query, profile=…, databases=…)` | → `SearchResult` | `query` is the one required answer; `databases` defaults to **all** repositories (MetaboLights + Workbench), candidates tagged `repository=` |
| `filter(screen(profile, min_survivors))` | SearchResult → `FilterResult` | drop hard-fails on search-index data; rank survivors; no truncation by default |
| `filter(maf(require, min_metabolites))` | InspectResult → `FilterResult` | drop MAF-poor studies (deep; place after inspect) |
| `filter(custom(name, **params))` | any → `FilterResult` | registered predicates — the extension point |
| `inspect(workers=10)` | → `InspectResult` | deep ISA download + parse; cache root keeps the ISA dirs |
| `score(profile=…)` | → `ScoreResult` | ranked + comparison table; neutral pass-through without profile |
| `describe(top=3, revision=0)` | ScoreResult → `DescribeResult` | per-sample sentences; ONE LLM call/study, store-cached per revision |
| `download(categories=…, dest_dir=".")` | InspectResult → `DownloadResult` | needs ≥1 constraint (refuses to download everything) |
| `export(path="manifest.csv")` | InspectResult → `ExportResult` | flat CSV + traceability |

All steps accept `cache=CacheOpts(enabled, ttl)` and `print=PrintOpts(top, detail)`; cache TTLs are generous by default (search 7d, inspect 30d, describe eternal).

### Repositories: which databases to search

`databases=("metabolights", "metabolomics_workbench")` is the default
(`DEFAULT_DATABASES`). Rules that make this painless:
- **`databases=("metabolights",)`** reproduces pre-workbench behavior exactly —
  use it when you only want EMBL-, ISA-based studies.
- The **workbench needs a cache root** (`pipeline.cache(root)` … `.cache("…")`
  is in The One Flow): its whole index is fetched once/7 d, then filtered
  locally. Pipeline/Wheat-free runs without a cache root must restrict
  `databases`.
- **Vocabulary ambiguity is an abstention, not a guess.** When a workbench
  slot term can't be matched confidently, it stays EMPTY and the result prints
  an actionable notice (top candidates + study counts + the exact next call).
  Follow it: refine the free text (or `profile.hard.diseases`) and re-run with
  `databases=("metabolomics_workbench",)`; MetaboLights results are untouched
  (per-repo cache).
- `StudyRequirements.diseases` is the structured disease channel (Workbench
  slot + vocabulary matching); free text is matched against titles client-side.

### Facets & vocabularies (read before searching)

Each repository has its OWN controlled values; the shared screen/profile
match them literally (substring, case-insensitive). Wrong vocabulary = silent
under-recall — read these before writing a profile:

- **MetaboLights `sample_types` are the index's `organismParts.term` facet
  values — case and wording matter.** For biofluids the canonical facets are
  `"blood plasma"` and `"blood serum"` (counts w/ query `Homo sapiens`: ≈202
  and ≈128); `"Serum"` (19), `"serum"` (31), `"Plasma"` (5), `"plasma"` (7)
  are residual variants. `sample_types=["Serum","Plasma"]` collapses the
  human-plasma universe to ~5 studies. Recipe: facet on the canonical
  variants (often with `free_text=""` or a one-word term) and push
  disease/condition filtering client-side (title/abstract/descriptors), since
  facets don't carry it.
- **Workbench `organisms` are the Latin names** (`"Homo sapiens"`); the
  metstat SPECIES slot maps latin→common itself (`Homo sapiens` → `Human`),
  and the shallow screen matches substrings — so
  `hard.organisms=["Human"]` silently drops every candidate. Use the Latin
  name and one shared profile serves both repositories.
- **Workbench SOURCE has no serum/plasma values** — the canonical biofluid
  source is `"Blood"` (plus variants like `Seminal plasma`); serum vs plasma
  must be verified per-sample from the factors payload after `inspect`
  (e.g. `sample_source = "Blood (plasma)"`). Don't claim serum/plasma from
  the search screen.
- **Workbench disease terms are title-cased canonicals** (`Cancer`, `Lung
  cancer`, `Alzheimers disease`) and the matcher prefers an exact value over
  a fuzzy subtype — use the exact canonical (`"cancer"` resolves to
  `"Cancer"`, never `"Lung cancer"`).

### Staged work (cheap first, deep later)

```python
probe = quick_probe("urine alzheimer", profile).cache(".pipeline_cache")
for tightened in profiles:                     # stage 1: narrow the profile,
    print(probe.run()["filter"].fmt())        #   no inspect, replays warm

deep = probe.extend(inspect(), score(profile))  # stage 2: only when settled
r = deep.run()
# continuing from a previous session/result: supply the typed result directly
r2 = pipeline(score(profile)).run(input=r["inspect"])
```

### Kick in the annexes when you want them (nothing is implicit)

```python
from metabo_search import maf, custom, download, export, register_predicate

p4 = (p
      .extend(filter(maf(min_metabolites=200)))        # drop MAF-poor studies
      .extend(describe(top=3))
      .extend(download(categories=["raw"], dest_dir="./data"))  # needs a constraint
      .extend(export(path="manifest.csv")))
r4 = p4.run(llm=call_llm)
```

### Direct single-call convenience

```python
report = quick_discovery("urine alzheimer", profile).run()       # == find_datasets(...)
all_done = full_report("urine alzheimer", profile, top=3).run(llm=call_llm)
```

---

## API table (legacy function surface — still works; the pipeline wraps these)

| Call | Returns | What it does |

| Call | Returns | What it does |
|------|---------|--------------|
| `find_datasets(query, profile, databases=...)` | `ComparisonReport` | **deterministic** discovery: per-repo search → shallow screen → deep-inspect survivors → score → rank. No LLM needed. |
| `screen_candidates(shallow, profile)` | `ScreeningResult` | deterministic hard-pass on search-index data + shallow rank (drop failures before the slow deep-inspect) |
| `profile_to_search_args(profile)` | `dict` | maps hard reqs to search API filters (server-side) |
| `filter_by_maf(deep, require_maf=..., min_metabolites=...)` | `[StudyCandidate]` | post-inspection: keep/exclude studies shipping MAF files |
| `download_maf_files(study_id, dest)` | `[Path]` | download just the `m_*.tsv` metabolite-assignment files |
| `analyze_maf_files(id, isa_dir\|maf_paths)` | `[MafAnalysis]` | **no-LLM MAF analysis**: #metabolites, #samples, names vs identifiers vs m/z-only |
| `render_maf_summary(analyses)` | `str` | paste-ready text block of the analyses |
| `prepare_samples(deep_study, store)` | `SampleTask` | bundles contexts + the ONE profile prompt |
| `load_samples(task)` | `[SampleDescription] \| None` | cached sentences, else None |
| `submit_samples(task, llm_profile_text)` | `[SampleDescription]` | applies your LLM recipe to all samples, caches |
| `list_data_files(deep_study)` | `[DataFileRef]` | recursive `FILES/` listing w/ sizes |
| `format_summary(deep_study)` | `dict[fmt→count]` | quick probe for mzML/RAW/.d presence (+ dir-aware raw/derived split) |
| `download_data_files(deep_study, DownloadConfig)` | `DownloadResult` | selective download (bg: `start_download`) |
| `SampleManifest.build(deep, ...)` | `SampleManifest` | flat CSV + traceability (optional) |
| `search_studies / inspect_studies / score_studies / build_comparison_table` | — | the pieces, if you want control |
| `load_study_from_isa(id, dir)` | `StudyCandidate` | offline: rebuild from local ISA files |

You construct: `RequirementProfile`, `StudyRequirements`, `DownloadConfig`.

---

## MetaboLights files vocabulary (read once, saves probing)

Each study ships ISA-Tab metadata + data files on
`https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/{MTBLS}/`:

| File | What it is |
|------|-----------|
| `i_Investigation.txt` | study title/abstract, organisms, publications, protocols |
| `s_*.txt` | sample file — one row per sample (names, characteristics, factors) |
| `a_*.txt` | assay file — sample → data-file links, instrument, ion mode |
| `m_*.tsv` | **MAF** (metabolite assignment) — one row per identified metabolite: name, formula, m/z, RT, species, database, per-sample abundance |
| `FILES/` | the actual data (raw/derived spectra) — listed recursively by `list_data_files` |

`candidate.metabolite_count` / `candidate.maf_files_parsed` tell you a MAF was
present and how many metabolites it lists. Use `filter_by_maf(candidates,
min_metabolites=N)` to require (or exclude) metabolite assignments after deep
inspection, or `download_maf_files(id, dir)` to fetch the MAFs themselves.
When a user asks “how many metabolites / samples, and does it have real names?”,
call `analyze_maf_files(id, isa_dir=...)` — it returns counts + a
`named | identified | mz_only` verdict with zero LLM calls; paste
`render_maf_summary(...)` into your answer. Metadata never exposes MS level —
confirm from a downloaded file or the paper.

---

## Deterministic vs AI — what needs the model

**Discovery is fully deterministic** once a `RequirementProfile` is structured:
server-side filter (hard reqs) → cheap shallow screen (drop hard-fails on the
search index, rank by soft score) → deep-inspect only survivors → score → rank.
No LLM in the loop (`report.screening` carries survivors + dropped-reasons so
you can explain *why* studies failed).

The model is only needed for two linguistic tasks:
1. Turning the user's **prose** into a `RequirementProfile` — skip if they fill
   the fields directly.
2. The per-study **sentence recipe** (1 cached call).

So if the researcher can state structured requirements, the whole collect→
analyze→summarize run is deterministic, reproducible, and fast.

## Sentence wording: what's in, what's out

The profile prompt enforces it, but OWN it when reviewing:
- **Include**: organism, tissue, disease/condition, biological state (fasted,
  baseline, post-challenge…), and biologically relevant factors (sex, age, group).
- **Exclude**: subject/participant IDs, barcodes, plate/well positions, and bare
  indices like "time point 1", "day 1", "replicate 3".
- **Rewrite** indices to meaning where the abstract supports it: "day 1 of
  fasting" → "after one day of fasting"; "time point 0" → "at baseline".
  Otherwise drop them.

## Wording feedback loop (until the user is happy)

Show the user 2–3 example sentences from the study. If the wording misses their
intent, revise and regenerate under a new revision — old wording is preserved
so they can compare:

```python
task   = prepare_samples(deep_study, store, revision=1)   # bump on each re-roll
descs  = load_samples(task)
if descs is None:
    descs = submit_samples(task, call_llm(task.profile_prompt))
```
Each `revision` gets its own cache slot, so feedback rounds never clobber each
other. Shortcut: `new_task = revise_samples(task, llm_text)` auto-bumps the
revision and caches; read results with `load_samples(new_task)`.

Codes that live in **factor values** (e.g. OGTT/OLTT/PAT/SLD) are decoded too:
the recipe's `code` slot accepts `field = <factor label>` (or `"data_files"`
to scan everything).

## What to decide (be fast, don't explore)

- **Discover** → `find_datasets` with the whole query + profile. Judge the top 2–3 by title/abstract/score.
- **RC/blank samples** → the library auto-tags them "quality control" — don't treat them as patients.
- **disease=unresolved** → check `desc.used_sources`. If a patient's disease didn't resolve (no linked files, unknown code), re-prompt the profile with the file-name codes or fix linkage. Don't accept silently.
- **Large studies** → always use a `SampleSentencesStore`; it's one LLM call and then forever free.
- **Reuse metadata** → `load_study_from_isa` if ISA files are already on disk; skip the network.
- **User error / API edge case** → note it and move on; don't loop.

## Errors & recovery
- **Datasets ≥8 with process parsing** → wrap your script's main code in
  `if __name__ == "__main__":` (spawn on macOS re-imports the entry script
  into each parse worker). In a throwaway script, set
  `MTBLS_PARSE_PROCESSES=0` to force thread parsing instead.
- `import metabo_search` resolves to a weird path → this repo shares a workspace
  with a parallel-test copy. Use the **private venv** so you always import THIS
  src: `uv venv .venv-local && uv pip install --python .venv-local/bin/python -e .`
  then run with `.venv-local/bin/python`. Never rely on the shared `.venv`'s
  editable pointer here.
- Downloads timeout → library retries with backoff+jitter; failing study stays
  shallow (don't loop).
- `.venv` broken → `rm -rf .venv && uv venv && uv pip install -e .`.

## Limits to be honest about
- **Free-text queries are phrase-based**: the API matches the whole string, so
  "urine alzheimer" often returns 0 while "alzheimer" finds the studies.
  Prefer one strong term and push everything else into the profile's hard
  filters (organism, min_samples, …); iterate wordings if a query returns 0.
- `min_samples` / `min_raw_files` are **client-side post-filters** over at
  most `max_results` fetched hits, in the API's default relevance order —
  raise `max_results` when heavy filters bite, and don't assume the returned
  set is the "best" matching set.
- The search index does NOT expose file formats or MS level. `format_summary()`
  can confirm mzML/RAW presence from filenames cheaply, but **MS1 vs MS2 can
  only be confirmed by opening a downloaded file or from the paper** — say so
  rather than guessing from the search result.
- **Workbench `metabolite_count` is a proxy, not a matrix dimension**: it is
  the length of the identified-metabolite list (`/metabolites`), with no
  per-sample abundance columns. The m×s signal matrix lives in mwTab
  datatables, which the library does not parse yet (metabolomics_workbench's
  `metabolite_table` is a stub). For “≥N metabolites” as a matrix requirement,
  prefer MetaboLights MAF numbers (`analyze_maf_files` reads the true
  per-sample matrix); quote workbench numbers as “ident. metabolite list
  length”.
- **One-match metstat pools and transient disconnects are handled**: the
  workbench client normalizes the API's flat single-study response and retries
  dropped connections; a failed/empty slot pool degrades to corpus matching
  with a notice — the pool is an optimization, never a hard filter.
