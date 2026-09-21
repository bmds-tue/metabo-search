# Plan: add Metabolomics Workbench (NMDR) as a second repository

Status: **plan only — no code written yet.** Research verified against the
live API, PyPI, and GitHub (2026-02). Package is `metabo_search`
(`src/metabo_search/`); the old `mtbls_agent` name is gone.

---

## 0. Summary (TL;DR)

Add **Metabolomics Workbench** as a second repository, searched by default
alongside MetaboLights. Public API changes are limited to:
`databases=` (which repos, **default all**) and a new
`StudyRequirements.diseases: list[str]` field (structured disease
matching; additive + optional, so old profiles stay byte-identical).

Reads: workbench = the whole index (4,580-study summary corpus +
disease/source/species maps) downloaded once, 7 d-cached, filtered **locally
(CPU-only)**; `metstat` is an optional fast-path used only for confident
matches. Matching: **rapidfuzz** + alias table + margin/confidence gate;
on ambiguity it **abstains** (never guesses) and prints an actionable
notice → optional `databases=("metabolomics_workbench",)` re-run + `input=`
composition (no new API). Caching is **per-database**; merged digests stay
by-value-deterministic. Agent discoverability is layered (in-band notice +
SKILL/references recipe). Per-repo code lives in `repositories/{metabolights,
workbench}/`; MS analysis, scoring, describe, manifest reuse existing steps.

**Main workflow deltas:** search loops repos (tag + merge) · vocabulary
matcher with abstain+notice · two-run composition for reformulation ·
inspect dispatches per `repository` · `datatable` acts as the MAF · describe
reads `factors` rows · download = datatable+mwtab (no raw spectra, MWB has no
HTTPS file API) · LLM parse prompt learns `diseases` · reports add a
repository column only when mixing.

**Known risks:** ~90 s cold corpus fetch; noisy disease vocab + alias-table
coverage; metstat ANALYSIS/POLARITY/CHROM slots deferred (probed,
vocab not derivable); two-run composition is
agent-side bookkeeping (mitigated by the notice); query→`diseases` is the one
LLM-nondeterministic step; per-db digest correctness; MWB raw-file download
out of scope v1; API version drift (spec v1.2 2025-07).

## 1. Goal & constraints

Add the **Metabolomics Workbench** (metabolomicsworkbench.org, NIH Common
Fund NMDR) as a second searchable repository next to MetaboLights.

Constraints (from the user):
1. **Public API does not change for now** — the *only* new option is
   `databases=` (which repositories to search); **default: all**.
2. **Repository-specific code stays separated** — one subpackage per
   repository (`repositories/metabolights/`, `repositories/workbench/`), no
   cross-coupling; shared machinery lives in `metabo_search/core/`.
3. No code yet — this doc is the implementation contract.

## 2. Current architecture (grounded, real names)

```
src/metabo_search/
├── __init__.py         # public surface (63 exports)
├── models.py           # StudyCandidate, StudyRequirements, RequirementProfile, ScoredCandidate, ComparisonReport...
├── client.py           # pooled httpx wrapper for the v2 search API          (MetaboLights-only)
├── searcher.py         # search_studies, profile_to_search_args (Solr POST)   (MetaboLights-only)
├── inspector.py        # inspect_studies, load_study_from_isa: ISA download+parse (MetaboLights-only)
├── scorer.py           # screen_candidates (deterministic shallow), _score_one   (generic ⚠ mixed)
├── maf.py              # analyze_maf_files: m_*.tsv parsing                    (MetaboLights-only)
├── downloader.py       # FILES/ listing + selective download                   (MetaboLights-only)
├── sample_gen.py       # per-sample sentence recipe                            (generic core)
├── summarizer.py       # comparison table                                      (generic core)
├── workflow.py         # find_datasets — thin shim over core pipeline          (generic)
└── core/               # THE typed pipeline — generic, repository-agnostic
    ├── steps.py        # Step, SearchConfig/InspectConfig/...; search()/inspect()/... factories; Pipeline
    ├── results.py      # 8 result types incl. SearchResult(candidates: list[StudyCandidate])
    ├── cache.py        # sha-keyed per-step cache, TTLs, warm replay
    ├── recipes.py      # quick_probe → quick_discovery → full_report → harvest
```

Key facts the plan relies on:
- **Pipeline kinds**: `search → filter(screen/maf/custom) → inspect → score →
  describe → download → export`; every step is `Config → Result`, cache-keyed
  on `sha(kind + config + input digest)`, results cross boundaries **by value**
  (deep copy).
- **`search_studies`** returns `list[StudyCandidate]`; shallow fields
  (`organisms`, `organism_parts`, `assay_techniques`, `sample_count`, …) feed
  the deterministic screen. Deep fields (`assays`, `sample_metadata`,
  `metabolite_count`, `sample_file_map`, …) come from ISA inspection and feed
  scoring + describe + download.
- **Score step** reads `StudyRequirements` (hard/nice) off
  `RequirementProfile.hard`/`.nice_to_have`; ion-mode/data-format/MAF criteria
  are *deep-only* (enforced post-inspection).
- IDs are **disjoint by construction** (MTBLS… vs ST…) → merging needs no dedupe.

## 3. Workbench REST API — verified facts

Base: `https://www.metabolomicsworkbench.org/rest/` · spec **v1.2 (07/22/2025)**
at `/tools/mw_rest.php` (PDF also offered). URL shape
`/rest/<context>/<input_item>/<input_value>/<output_item>[/format]`, JSON
default, **single-page, no pagination**.

| Endpoint | Payload / time (measured) | Role |
|---|---|---|
| `study/study_id/ST/available` | all PR/ST/AN triples · 600 KB · ~8 s | baseline list |
| `study/study_id/ST/summary` | **4,580** per-study summaries · 2.6 MB · **~90 s** | free-text corpus (cache 7 d) |
| `study/study_id/ST/disease` | study→disease map · 162 KB · ~1.8 s · **distinct = 259 values (the disease vocab)** | screening enrichment + client-side disease filter |
| `study/study_id/ST/source` (and `ST/species`) | study→tissue / →species · small · fast · **distinct = 328 source / 506 species values** | screening enrichment + vocab derivation |
| `metstat/<8 slots>` | matching rows (study,title,species,source,disease) · KB · instant | **server-side search** |
| `study/study_id/ST000001/summary` | one study summary | deep inspect |
| `study/study_id/ST000001/factors` | per-sample rows (local_sample_id, sample_source, factors, mb_sample_id) | ISA sample-sheet analog |
| `study/study_id/ST000001/analysis` | per-analysis (MS/NMR type, ion mode, chromatography, instrument) | ionization/technique (deep) |
| `study/study_id/ST000009/metabolites` | identified metabolites per study | MAF-ish |
| `study/analysis_id/AN000001/datatable` | identified-metabolite table | **MAF equivalent** |
| `study/analysis_id/AN000001/mwtab` | mwTab doc (JSON/txt) | full-fidelity dump |

`metstat` slots: `ANALYSIS;POLARITY;CHROM;SPECIES;SOURCE;DISEASE;KEGG;REFMET`,
empty = wildcard (e.g. `/metstat/;;;Human;Blood;Diabetes`).

### metstat — verified semantics (probed live)
- **Slots AND**: `Human`=1503 → `Human;Blood`=468 → `Human;Blood;Diabetes`=30
  (strict subsets; disease-map `Diabetes` x138 is that slot's ceiling).
- **Case-insensitive** values (`human`≡`Human`, `HUMAN`); empty slot = wildcard.
- **Exact canonical values, no synonyms/substrings**: `Alzheimers`→0 vs
  `Alzheimers disease`→4; `Type 2 Diabetes`→0. A near-miss silently drops
  everything — this is why the matcher MUST map/abstain before calling.
- **SPECIES takes common names only** (`Mouse` 75, `Rat` 246); Latin rejected
  (`Rattus norvegicus`→0). Organisms must map→common via the `ST/species` map
  (Latin + Common names).
- **ANALYSIS accepts broad codes** (`MS` 3747, `NMR` 156, `LCMS`, `GCMS`); its
  canonical vocab ≠ summary `analysis_type` strings. **POLARITY alone → 0**
  (quirk; polarity/chrom interplay not fully understood).
- **No-match / unknown value → empty response** (clean signal).

**v1 slot policy (consequence):** fill **SPECIES / SOURCE / DISEASE** only
(all three verified + covered by the `ST/*` maps); leave
ANALYSIS/POLARITY/CHROM/KEGG/REFMET empty — their canonical vocab isn't
derivable from the maps and semantics are partly unknown; the corpus backstop
covers what they would add.

Limitations: **(1)** no free-text REST search — titles live in the cached
`ST/summary` corpus; **(2)** no HTTPS endpoint for raw spectra files (FTP
only) → v1 download = `datatable` + `mwtab`, raw-file download is out of
scope; **(3)** disease/source are controlled lists (not ontology terms).

## 4. Library landscape (researched)

- **No maintained Python REST client** for this API (PyPI: absent; GitHub:
  only 0-star experiments; the official reference client `metabolomicsWorkbenchR`
  is R/Bioconductor).
- `mwtab` (PyPI 2.1.1) is the official mwTab parser — **optional**, we can use
  the JSON endpoints instead and add **zero new dependencies**.

**Decision:** hand-roll a thin pooled-`httpx` wrapper modeled on the existing
`client.py`/`inspector.py` patterns (keep-alive, retry+backoff+jitter, TTL caches).

## 5. Target architecture — per-repository separation

Two sibling subpackages behind a tiny seam; `core/` stays generic and
unchanged; top-level module names keep working as re-export shims (so the
public API surface is untouched by the move).

```
src/metabo_search/
├── __init__.py  models.py  workflow.py  summarizer.py  sample_gen.py   # unchanged (re-export shims added)
├── core/                                   # UNCHANGED typed pipeline
└── repositories/
    ├── base.py                             # StudyRepository ABC, DISPATCH, database_from_id()
    ├── metabolights/                       # ← moved from today's top-level modules (pure move, no logic change)
    │   ├── __init__.py                     #   MetabolightsRepository (adapts legacy entry points)
    │   ├── client.py  searcher.py  inspector.py
    │   ├── maf.py  downloader.py
    └── workbench/                          # NEW
        ├── __init__.py                     #   WorkbenchRepository
        ├── client.py                       #   pooled httpx wrapper (per above)
        ├── endpoints.py                    #   URL builders + response shapes
        ├── search.py                       #   metstat fast-path + corpus free-text filter
        ├── inspect.py                      #   parallel deep GETs + normalization
        ├── datatable.py                    #   datatable → MAF-shape normalization
        ├── matcher.py                      #   rapidfuzz engine + alias table + margin/confidence decision
        ├── slots.py                        #   metstat slot assembly (species/source filled only on CONFIDENT match)
        ├── download.py                     #   datatable + mwtab retrieval
        ├── corpora.py                      #   ST/summary + disease/source maps caching + vocab derivation
        └── vocab.py                        #   canonical disease/source/species lists (vendored snapshot + refresh)
# decorators: searcher.py inspector.py downloader.py maf.py  → thin `from metabo_search.repositories.metabolights import *` shims
```

### The seam — `repositories/base.py`

```python
class StudyRepository(ABC):
    name: ClassVar[str]                       # "metabolights" | "metabolomics_workbench"
    def search(self, query, *, profile, page_size, max_results,
               filters=None, organism=None, technique=None,
               sample_type=None, min_samples=None,
               min_raw_files=None) -> list[StudyCandidate]: ...
    def deep_metadata(self, candidates, *, workers=10,
                      tmp_dir=None, parse_workers=None) -> list[StudyCandidate]: ...
    def sample_rows(self, c: StudyCandidate) -> list[dict[str, str]]: ...    # describe/manifest
    def metabolite_table(self, c: StudyCandidate) -> MetaboliteTable | None: # MAF/datatable
    def download(self, candidates, dest, *, files_cache_dir=None) -> list[Path]: ...

DISPATCH: dict[str, StudyRepository]            # registered at import
def database_from_id(study_id: str) -> str      # "MTBLS…" → metabolights, "ST…" → workbench
```

`MetabolightsRepository.search`/`.deep_metadata`/… **delegate to the moved
modules verbatim** (the legacy entry points become thin wrappers over the
repository, or vice versa — whichever keeps `metabo_search/searcher.py` etc.
as importable shims). `WorkbenchRepository` is a from-scratch implementation.

### Model change (forward-compatible)

`StudyCandidate` gains one field:

```python
repository: str = "metabolights"   # "metabolights" | "metabolomics_workbench"
```

It is serialized in `to_json`/`from_json` and part of the input digest (a
workbench candidate and a metabolights candidate are different cache inputs —
this is the merge-correctness keystone).

## 6. Public API — the only user-visible change

`databases: tuple[str, ...] = DEFAULT_DATABASES` where
`DEFAULT_DATABASES = ("metabolights", "metabolomics_workbench")`, on **every
public search entry point**:

- `core/steps.py::search(...)` → `SearchConfig.databases`
- `core/recipes.py`: `quick_probe`, `quick_discovery`, `full_report`, `harvest` (forwarded)
- `workflow.py::find_datasets(...)` (forwarded)
- `searcher.py::search_studies(...)` (via shim → repository)

Semantics:
- **Validation at build/`validate()` time**: unknown name → `ValueError`
  listing valid names; empty tuple → error (must pick ≥1).
- **Default = both** → today's behavior *plus* the new repository; passing
  `databases=("metabolights",)` is byte-identical to current output (asserted
  by test).
- **Merge**: results concatenated in configured order, each candidate tagged
  `repository=`; no dedupe (IDs disjoint). `SearchResult.args_used` records
  the resolved `databases`.
- Reports/summaries: `build_comparison_table` and describe output add a
  `repository` column only when ≥2 repositories present (so `("metabolights",)`
  prints *exactly* like today — the "API does not change" guarantee).
- **Ambiguity handling — no new API.** Workbench slot terms are derived from
  `query`/`profile` automatically. On a non-confident match the slot stays
  EMPTY (never a guess) and the result carries a notice: top candidates with
  scores + study counts, "none entered due to ambiguity — reformulate to
  disambiguate", plus the recommended scoping: "to re-run only the workbench
  search, set `databases=("metabolomics_workbench",)` — MetaboLights results
  and cache entries stay untouched". The workbench "index" (summary corpus +
  disease/source maps) is downloaded whole and cached (7 d TTL), so
  workbench-side search/reformulation is **local CPU after the first fetch**;
  the MetaboLights side is one pooled Solr POST, 7 d-cached. Reformulation is
  **optional** — the corpus backstop already searched workbench
  deterministically; a reformulation only upgrades the workbench path to
  server-side `metstat` slot filtering.

## 7. How it slots into the pipeline (concrete call paths)

All step kinds are unchanged; their bodies dispatch per repository.

**search step** (`_do_search`):
1. for each `db` in `cfg.databases`: `cands = DISPATCH[db].search(...)`
   - metabolights: current `search_studies` + `profile_to_search_args`
     (server-side filters) — verbatim.
   - workbench: **disease is matched client-side** — deterministic
     term-match over the cached `ST/disease` map, applied against
     `profile.diseases` when set, else against free-text-derived terms; the
     `metstat` DISEASE slot is filled only when `profile.diseases` is set AND
     the matcher is confident — otherwise empty (wildcard), because a
     guessed controlled-list value silently drops correct studies. **fast
     path** `metstat` is used for disease (when confident) and
     species/source/analysis slots when they map *confidently* from the
     profile; **backstop** deterministic local
     term-match over the cached `ST/summary` corpus (7 d TTL, one ~90 s build)
     covers everything else, incl. broad free text; hits are then enriched
     with the disease/source maps for screening.
2. tag `candidate.repository`, concat in `db` order → `SearchResult`.

**Disease & tissue matching — agreed contract:**

| Concern | Decision |
|---|---|
| disease filter | deterministic term-match over the cached `ST/disease` map (applied during search/screen); matches against `profile.diseases` **when the field is set (recommended)**, else free-text-derived terms |
| `metstat` DISEASE slot | filled **only when** `profile.diseases` is set AND the matcher is confident (rapidfuzz + margin); otherwise empty (wildcard) — abstain never guesses, a wrong guess silently drops correct studies |
| tissue (`SOURCE` slot) | filled only when `sample_types` maps *confidently* to the controlled list; otherwise corpus/source-map term-match |
| species (`SPECIES` slot) | filled from `organisms` (clean mapping); empty when unsure |
| guarantee | matches MetaboLights semantics: term either hits the map or not; screening & scoring rank everything; no manual review step |
| vocabulary risk | matcher tests (`test_workbench_matcher.py`) + pinned snapshots; re-capture fixtures on list changes |

**Structured fields vs free text — recommended direction (agreed with user).**
Add `diseases: list[str]` (hard + nice) to `StudyRequirements`; keep
`organisms`/`sample_types`/`techniques`/`analysis_types`/`ionization_modes` as
-is. Rationale: going query→fields is the single hard step (the LLM's
prose→profile parse, which already exists), while fields→query is
deterministic — so the library side becomes entirely the easy direction.
Consequences for the workbench: (1) the `DISEASE` slot upgrades from
"never filled" to "filled when the field is set and confident";
(2) ambiguity becomes rarer and localized — the reformulation loop shrinks to
refining `profile.hard.diseases` (a config change, per-config cache-keyed, no
no free-text blast radius); (3) the in-band notice/abstain mechanics stay, with
better input. Guardrails: additive + optional (old profiles byte-identical,
API-stable); only add fields that map to a concrete filter in ≥1 repository;
`free_text` remains for ranking/boosting and unparseable nuance. Cost: one
LLM-parse prompt change + resist field-creep (publication-year/study-type/etc.
stay free text unless a repo filter demands them).

**filter step (`screen`)**: unchanged. Workbench candidates populate the same
shallow fields: `organisms=[OntologyTerm(species)]`,
`organism_parts=[OntologyTerm(source)]`, `assay_techniques` from
`analysis_type`, `sample_count` from `number_of_samples`,
`design_descriptors` from disease map. Ionization/formats stay deep-only (as today).

**inspect step** (`_do_inspect`): split the candidate list by `repository`;
metabolights path = current `inspect_studies`; workbench path = parallel
`deep_metadata` (per study: GET `summary`+`analysis`+`factors`+`metabolites`,
~4 tiny requests, same pooled client + sync-thread pool + download→normalize
overlap trick; parse is light here, threads suffice). Merge back into one
`InspectResult`. Deep fills: `assays` (analysis endpoints), `sample_count`
and `sample_metadata`/`sample_metadata_fields` (factors rows),
`metabolite_count` (metabolites/datatable count, **same fullest-column rule**),
`inspection_depth=deep`.

**score step**: unchanged. `metabolite_count` + `has_maf`
(`metabolite_table()` not None) feed the existing hard/nice MAF criteria via a
small adapter: `datatable` ✓ (datatable present → has_maf equivalent).

**describe step**: unchanged recipe — `prepare_samples(deep)` reads
`sample_metadata` (+ `factors` context for workbench that include
`disease` when the study is disease-associated). Still **1 LLM call/study**,
still cached by `(study_id, data hash, revision)`.

**download/export steps**: dispatch — metabolights = current
`list_data_files`/`download_data_files`; workbench = download `datatable`
(identified-metabolite TSV) + `mwtab` per analysis into the existing
`{dest}/{study_id}/` layout. Raw-spectra download = documented unsupported
(FTP-only). SampleManifest works for both via `sample_rows()`.

**Cache**: step keys already include config + input digest → `database` in
`SearchConfig` and `repository` in serialized candidates change keys correctly;
warm replay and `run(force=True)` behavior unchanged.

**Per-database search caching (one pipeline over multiple databases).**
Precise scheme:

```python
per_db_key(db)     = sha(db_name + db_scoped_config + effective_query_for_db + profile_args_for_db)
merged_result      = concat(per_db[db].candidates for db in ordered databases)  # + repository tags
merged_step_key    = sha(kind="search" + SearchConfig.full_digest)               # existing step-key form
```

`effective_query_for_db` is the query string that db sees — identical across
dbs today; a reformulation makes one of them differ. The merged `SearchResult`
is a **pure function** of the per-db pieces + the ordered `databases` tuple
(not a separately-stored entity), so fresh==warm holds by construction;
`merged_step_key` is the only thing required for correctness, and the per-db
sub-caches are the optimization that makes `databases`-subset changes reuse
every unchanged db (no re-fetch, no cross-invalidation). Consequences:
(1) adding/removing a db reuses unchanged outcomes; (2) a workbench-only
iteration run (`databases=("metabolomics_workbench",)`) leaves the
MetaboLights cache entries untouched; (3) first `ST/summary` (+ disease/source
maps) I/O happens once per TTL regardless of run count; everything later is
CPU.

**Reformulation & composition — what the API is (and isn't).** If a
reformulation changes the query for *all* selected dbs, both per-db keys
change and both re-run (ML: one pooled POST; workbench: CPU over cached
corpus). Keeping ML results from the original query while reformulating only
the workbench **cannot be a single run when dbs share one query string** — a
shared query can't differ per db. The design therefore uses the **existing
midstream `input=` API** (already supported by `pipeline(...).run(input=...)`):

```python
r1 = quick_discovery("urine alzheimers", profile, databases=("metabolights", "metabolomics_workbench")).run()
r2 = quick_discovery("urine alzheimer's disease", profile, databases=("metabolomics_workbench",)).run()
merged = SearchResult(candidates=r1["search"].candidates + r2["search"].candidates)
pipeline(inspect(workers=10), score(profile=profile)).run(input=merged)
```

Candidates are `repository`-tagged, so inspect dispatches per db; the inspect
input digest includes `repository`, so this is deterministic and by-value. A
per-db query override in `SearchConfig` (`db_search=…`) would let one run
express both queries — **explicitly rejected**: it's new API that splits
intent (two strings to keep consistent), duplicates what `input=` already
achieves, and belongs to the same family of complexity that killed
`disambiguate()`/`term_resolutions`. The scoping hint in the ambiguity notice
points the agent at the two-run `input=` flow for exactly this reason.

**Canonical ambiguity workflow (4 steps, no new API):**
1. **query all dbs** — `databases=("metabolights", "metabolomics_workbench")`,
   q₁; workbench returns corpus-backstop results + the ambiguity notice
   (top-N candidates, counts, scoping hint).
2. **split the query** — run 2: `databases=("metabolomics_workbench",)`, q₂
   (refined free text). ML is *not invoked at all* here; workbench recomputes
   locally over the cached corpus (CPU-only).
3. **reuse ML as-is** — its candidates come from run 1's `SearchResult` (no
   re-run; replaying run 1 later is a warm cache hit ~0s).
4. **combine** — `SearchResult(candidates=r1_ml + r2_mwb)` →
   `pipeline(inspect, score, ...).run(input=...)`; `repository` tags drive
   dispatch downstream.

Notes: run 2's q₂ can itself be ambiguous (another notice, same loop — cheap);
the fact that run 2 never invokes the ML pipeline is what makes the
"MetaboLights results kept as-is" guarantee structural, not a cache
heuristic.

**How the agent knows this flow (discoverability contract).** The flow is not
obvious from the signature alone, so the design makes it *self-revealing at
runtime*: the ambiguity notice is an **actionable recipe, not a diagnostic** —
the same text appears in `fmt()`, `args_used`, and the references/SKILL recipe
— stating exactly what to do next (refine `free_text`, re-run with
`databases=("metabolomics_workbench",)`, then combine via
`pipeline(input=SearchResult(...))`). Layered fallbacks: (1) the in-band
notice reaches any agent with zero docs; (2) SKILL.md / `references/api.md`
carry a copy-paste "ambiguous workbench match" recipe for harnessed agents;
(3) composition reuses the pre-existing midstream `input=` API — the snippet
adds no new surface. Test contract: the notice string must contain the
concrete next call (refined-phrase suggestion + the `databases=` call + the
`input=` combination step), and the documented recipe must match it.

**Vocabularies are a solved problem** (verified): the workbench's own
controlled lists (disease 259, source 328, species 506 distinct values)
are *derived directly from* `ST/disease`/`ST/source`/`ST/species` — the same
endpoints the plan already calls. They are vendored as snapshot fixtures
(`tests/fixtures/workbench/vocab_*.json`, refreshed by a small script) so
`matcher.py` only maps **user phrasing → known list value** (apostrophe/case/
plural normalization + a small alias table instead of a from-scratch lexicon),
and the client-side disease matcher is exact-keyword + normalized, testable
against the fixed list. Caveats: the disease list is noisy (synonym values
coexist: `Dengue`/`Dengue Fever`, `Alzheimers disease`), and source mixes
tissues/cell-types/misc — both handled by additive term-match, not exclusion.

**Term matching — decided: rapidfuzz engine + thin decision layer, no vector
engine** (validated against the real 259/328/506 lists and the real library).
Library research: `rapidfuzz` v3.14 (zero runtime deps, C++ speed, industry
standard) ships `process.extract(query, candidates, scorer=…,
score_cutoff=…)` — the exact primitive needed. `text2term` (purpose-built
free-text→controlled-vocab mapper) evaluated & rejected: heavy dep tree
(pandas/sklearn/owlready2, OBO-file oriented). `thefuzz`/`textdistance`/
`py_stringmatching` rejected (slow/unmaintained). So `matcher.py` calls
rapidfuzz (`WRatio`, `score_cutoff`) and adds only a **decision layer**: a
curated alias table for the measured semantic gaps (`plasma`→`Blood`, generic
`cell line`→`Cultured cells`), species-aware candidate prefiltering
(disambiguates `brain`), and a score+margin confidence rule. Empirical results
on the real lists: `alzheimers`→`Alzheimers disease` 81 (2nd 50),
`parkinsons disease`→94.4, `diabetes`→87.5, `urine`→80 — clean margins;
`plasma`→`Mycoplasma mycoides` (90/90/90) and `brain`→Bee Brain vs Brain
(80/80) are over-match/ties the decision layer abstains on (→ corpus
backstop). **The agent never reads the lists** — matching is automatic inside
the library, and the decision is surfaced as `(value, score, margin,
matched_tokens)` for visibility. **Testing posture**: similarity math is
rapidfuzz's (its own suite); ours is the decision layer, tested by resolvable
outcomes on fixtures (match / abstain / alias-bridge) — no hand-rolled scoring
to debug. **Safety property**: on a non-confident match the metstat slot is
NOT filled and the deterministic corpus backstop handles it — a failed match
can never exclude correct studies. Vectors are deferred to future
study-summary *ranking* (Phase 3), where a vector engine pays off; mapping to
259/328 short values is the wrong scale.

**Ambiguity handling — empty slot + informative notice.** If the matcher is
not confident (score or margin below threshold), the slot is left EMPTY and
the workbench result prints: (1) the top candidates with scores and study
counts (`ST/disease`: `Diabetes` x138, `Alzheimers disease` x27…), (2) the
notice "none entered due to ambiguity — reformulate the search profile to
disambiguate", (3) the **scoped re-run note**: a reformulation is a new
search; by default all selected repositories run the new query (MetaboLights:
one pooled Solr POST, 7 d-cached; a phrase refinement *refines* hits, not
resets them) — **or** scope it with `databases=("metabolomics_workbench",)`
to re-run only the workbench side (local CPU over the cached corpus, instant)
and keep MetaboLights results + cache untouched. The corpus backstop has
already returned workbench results deterministically, so the reformulation is
optional and only upgrades the workbench path from corpus-term-match to
server-side `metstat` slot filtering.

**Why no per-repository term channel** (design rationale, recorded): the query
is the single intent source (free-text focus); the library is stateless, so
any refinement is a new run/search by definition. A workbench-only
disambiguation channel would add API surface and split intent — deliberately
rejected. Consequence: a reformulation is a new search; by default it runs all
selected repositories, but `databases=` scoping (per-db cache) lets the agent
re-run only the workbench side — so the re-run cost is always an explicit,
scoped, deliberate agent decision.

**Decisions are never silent.** Every search result carries the resolved
vocabulary decisions and abstentions in `fmt()` + `args_used` (per repository:
which query/terms ran, which slots stayed empty and why), so a later agent or
audit sees exactly what happened. This is the "don't early-return, print what
was decided" option — always on, not a mode.

## 8. Implementation milestones (each ends green)

**M0 — cleanup (done elsewhere):** `mtbls_agent` renamed to `metabo_search`.
Remaining: drop the stale `mtbls_agent-0.1.0.dist-info` from the venv.

**M1 — repository refactor (pure move, zero behavior change):**
- create `repositories/metabolights/` by moving `client.py`, `searcher.py`,
  `inspector.py`, `maf.py`, `downloader.py` body (git mv + header edits);
  leave thin `metabo_search/{searcher,client,inspector,maf,downloader}.py`
  re-export shims.
- add `repositories/base.py` (ABC + `DISPATCH` + `database_from_id`).
- move nothing in `core/`, `models.py`, `scorer.py`.
- **Gate:** full `tests/` suite green; `import` surface identical; diff is
  mechanical.

**M2 — `databases=` plumbing (the only API change):**
- `SearchConfig.databases`; `search()`/`find_datasets()`/recipes forward it;
  validation on build; `_do_search` loops DISPATCH.
- register `MetabolightsRepository` (delegates to M1 modules).
- merge + tag `repository`; serialize `repository` in results JSON;
  `fmt()`/table show the column only when mixed.
- **Gate:** new tests — default=both, `("metabolights",)` byte-identical to
  pre-change output, bad-name ValueError, merge order, fresh==warm.

- **Dependency (accepted):** `rapidfuzz>=3.14` — the only new runtime dep
  (zero deps itself, C++ wheels); `text2term`/`thefuzz`/`textdistance`
  evaluated, rejected.

**M3 — workbench client + search:**
- `client.py` (pooled httpx, retries), `endpoints.py`, `corpora.py`
  (summary/disease/source maps, 7 d TTL in the step cache root),
  `matcher.py` (rapidfuzz engine + alias table + margin/confidence gate),
  `vocab.py` (vendored disease/source/species snapshots + refresh script),
  `search.py` (metstat fast path + corpus backstop + enrichment),
  `WorkbenchRepository` registration.
- **Gate:** offline fixtures for metstat/corpus/disease; live smoke of one
  structured + one free-text query (assert ST ids + tagged fields).

**M4 — workbench deep inspect + scoring hook:**
- `inspect.py` parallel GET + normalization; `datatable.py`
  (fullest-column count, named/identified/mz annotation levels reusing the
  maf.py classification rules); screen/score already work via fields;
  `filter_by_maf` adapter.
- **Gate:** fresh==warm determinism, threads==procs, deep-only criteria
  enforcement, `has_maf`/`min_metabolites` on workbench studies.

**M5 — describe/download/export:**
- `sample_rows()` for factors; `download.py` datatable+mwtab into
  `{dest}/{study_id}/`; SampleManifest for workbench studies.
- **Gate:** manifest build, download layout, sentence recipe determinism
  (1 LLM call/study, revision-keyed cache).

## 9. Test plan (concrete files, offline-first)

Fixtures in `tests/fixtures/workbench/` captured once from live API (no
network in tests): `metstat_diabetes.json`, `summary_corpus.json` (small
subset incl. disease/source maps), `vocab_disease.json` / `vocab_source.json` / `vocab_species.json` (full 259/328/506 value lists), `study_ST000001_{summary,factors,analysis,metabolites}.json`,
`datatable_AN000001.json`, `untarg_AN000113.json`, plus one title-rich corpus
slice for free-text tests.

| Test file | Asserts |
|---|---|
| `tests/test_database_option.py` | default=both; `("metabolights",)` == pre-change output byte-for-byte; unknown db → ValueError; empty → error; merge order + `repository` tag; `args_used.databases` |
| `tests/test_repositories_registry.py` | DISPATCH contents; `database_from_id("MTBLS…")`/`("ST…")`; shims re-export identically |
| `tests/test_workbench_client.py` | URL builders (all endpoints), response unwrap, retry/backoff (monkeypatched transport) |
| `tests/test_workbench_search.py` | metstat row→StudyCandidate normalization (species/source/disease→organisms/organism_parts/design_descriptors, titles); corpus free-text filter determinism + ranking; enrichment merge; empty-slot metstat → corpus fallback |
| `tests/test_workbench_vocab.py` | vocab snapshots match pinned counts (259/328/506); every alias maps onto a real list value; case/apostrophe/plural normalization |
| `tests/test_workbench_matcher.py` | real-phrase→real-list matches (`alzheimer`→`Alzheimers disease`, `diabetes`→`Diabetes`, `parkinsons`→`Parkinsons disease`, `urine`/`brain`/`kidney` exact); alias bridges (`plasma`→`Blood`, `cell line`→`Cultured cells`); low-confidence → no match (corpus backstop); margin/threshold logic; matched-token transparency |
| `tests/test_workbench_ambiguity.py` | ambiguous term → slot empty, notice (top-N + scores + study counts + "reformulate" guidance + `databases=("metabolomics_workbench",)` scoping hint) in `fmt()`; abstention recorded in `args_used` per repository; confident → slot filled; corpus backstop ran regardless (workbench results present without the slot); **no new API surface** (only query/profile refines matching) |
| `tests/test_workbench_cache.py` | per-db keys = sha(db + config + effective query + args); merged result is a pure concat (by-value, tagged); adding/removing a db reuses unchanged outcomes; workbench-only re-run leaves ML cache untouched; fresh==warm across `databases` changes |
| `tests/test_workbench_composition.py` | two-run reformulation flow: run all (q₁) + run workbench-only (q₂), merge via `SearchResult(input=…)` → inspect dispatches per `repository`; merged calculation deterministic (fresh==warm); ML results in the composed run are byte-identical to q₁-only run; no `db_search=` override exists |
| `tests/test_workbench_notice_actionable.py` | the ambiguity notice is an actionable recipe: contains the ambiguous term, top-N candidates + counts, a refined-phrase suggestion, the `databases=("metabolomics_workbench",)` call, and the `input=` combination step; `args_used` mirrors it; the references/api.md + SKILL.md recipe matches it |
| `tests/test_workbench_inspect.py` | summary+factors+analysis normalization: `assays[].ionization_mode/technique`, `sample_count`, `sample_metadata`, deep flags; parallel determinism; threads==procs; mixed-repo by-value handoff (digest includes `repository`) |
| `tests/test_workbench_datatable.py` | fullest-column metabolite count; named/identified/mz levels; `filter_by_maf(True, N)` on workbench candidates; `has_maf` hard/nice scoring |
| `tests/test_core_workbench_pipeline.py` | `quick_discovery(databases=("workbench",))` fresh==warm; cache replay; `("metabolights",)` unchanged; mixed pipeline end-to-end with fixtures |
| `tests/test_workbench_download.py` | datatable+mwtab to `{dest}/{study_id}/`; manifest from factors rows; revision cache |
| `tests/test_linking_workbench.py` | factors→data-sample linking quality on real ST studies (mirrors `test_linking_realworld.py`) |

**Live runs** (guarded scripts, no assert-heavy): one structured + one
free-text query across both repos; record wall-times + payload sizes into
`docs/perf.md` (new section) — mirrors the existing perf discipline.

## 10. Risks & open questions

- **`diseases` field (recommended, one-time API delta beyond `databases=`)**
  narrows the ambiguity loop and enables confident `DISEASE` slot fills;
  needs an LLM-parse prompt change + keep field-creep bounded. Without it,
  disease stays free-text-derived with the abstain on ambiguity rule intact.
- **~90 s cold `ST/summary`** corpus build — absorbable via the 7 d step
  cache; alternate: build lazily in the background. Decide in M3.
- **Disease matching semantics** (agreed contract): deterministic term-match
  over the cached `ST/disease` map + the corpus backstop guarantee recall and
  full automation; the `metstat` DISEASE slot is filled only when
  `profile.diseases` is set AND confident, else empty (abstain never guesses).
  Adding the `diseases` field (see bullet above) is the recommended upgrade;
  free-text-derived disease (no field) keeps the rule: abstain on ambiguity.
- **Controlled vocab drift** (disease/source lists change) — vocab snapshots are
  pinned by count + alias-sanity tests; the refresh script re-captures them on
  upgrade. Assumption to verify in M3: REST maps' distinct values == site
  pulldowns' lists (same backing table, not yet hand-checked).
- **No HTTPS raw-file download** — explicit v1 non-goal (`datatable` +
  `mwtab` cover metabolite-level needs; MetaboLights remains the raw-file repo).
- **Cache correctness**: `repository` must be part of serialization + digests
  from day one (M2), or mixed-repo warm replay will diverge (the exact bug
  class the by-value fix addressed). Search caching is **per database** so
  `databases=` changes never cross-invalidate; merged digest = ordered
  `databases` + per-db digests.
- Version pin: spec v1.2 (07/22/2025); re-fetch docs/PDF on integration.