# Parallel-agent audit — verification + documentation plan

Source: the `mtbls-wb` usage report ("human serum/plasma, cancer, ≥500 samples,
≥200 metabolites"). This file records (A) what was verified against the code
and live APIs, (B) bug verdicts with fix locations, (C) the documentation plan
to smooth out the misunderstandings — **no code changed as part of this audit
(yet)**. Fixes for the confirmed bugs will follow in a separate pass.

> **Status: RESOLVED.** The three confirmed bugs are fixed with regression
> tests in `tests/test_parallel_agent_regressions.py` (2.1 flat-record
> normalization + defensive `metstat_matches`, 2.2 exact-value pre-pass in
> `matcher.match`, 2.3 bounded retry in `client.metstat`); the documentation
> plan in section C is applied (SKILL.md "Facets & vocabularies" +
> Limits bullets, `references/api.md`, and the docstrings listed in C4).
> Live re-verification: `;;;Human;Blood;Lung cancer;;` → `{"Row1":
> "ST003989"}` (no crash), `match("cancer")` → `"Cancer"` (not
> `"Lung cancer"`). Full suite: 164 tests green.

Live checks run 2025-09-21 against `https://www.ebi.ac.uk/metabolights/ws3` and
the metabolomicsworkbench REST API, plus the cached live workbench corpus and
the offline fixtures.

---

## A. Bug verdicts (Section 2 of the report)

| # | Claim | Verdict | Details |
|---|-------|---------|---------|
| 2.1 | metstat crashes on exactly-one study | ✅ **REAL, reproduced** | Live `;;;Human;Blood;Lung cancer;;` → API returns a **flat record** `{"study": "ST003989", "study_title": …}` instead of `{"RowN": …}`; `metstat_matches` iterates `rows.values()` and calls `.get("study")` on a str → `AttributeError: 'str' object has no attribute 'get'`. **Partly mitigated since the last fix session:** `_metstat_pool_best_effort` now catches this and falls back to corpus-wide ranking with a notice — but the correct slot pool is lost and results diverge from intent. |
| 2.2 | "cancer" → "Lung cancer" (semantic narrowing) | ✅ **REAL, reproduced** | Live vocab `match("cancer")` → `Lung cancer` (score 90, margin 6.7 > 2, **confident**) although canonical `Cancer` exists. `WRatio("cancer","Cancer")`=83.3 (case penalty) < `WRatio("cancer","Lung cancer")`=90 (partial-ratio boost). Fills the DISEASE slot with the subtype → metstat pool = lung-cancer only → other cancer studies starved (violates the never-starve contract). |
| 2.3 | multi-word slots + single-record bug compound | ⚠️ **Mostly reduces to 2.1** | Spaced values with ≥2 matches return the normal `{"RowN": …}` shape (both raw spaces and `%20` encode fine — httpx handles them); the flat record is the single-match case regardless of spacing. **Additional real flakiness:** a broad slot search (`;;Human;Blood;;`) hit `RemoteProtocolError: Server disconnected` — the shared client has **no retry** on metstat (unlike the corpus fetch). |
| 2.4 | `/mwtab` inconsistent; datatable is a stub; metabolite count is a proxy | ✅ **REAL (documented stub)** | `workbench/datatable.py` explicitly returns `[]` until the "MAF adapter milestone". `/metabolites` rows are `study_id / analysis_id / analysis_summary / metabolite_name / refmet_name` — **zero per-sample abundance columns** (fixture + design confirmed). So `metabolite_count` is an identified-metabolite list length, NOT a matrix row count. The m×s signal matrix (mwTab datatables) cannot be verified by the library today. |
| 2.5 | docs gaps | ✅ **CONFIRMED — the target of part C** | See documentation plan. |

**Not bugs / already-fixed-from-earlier pass, for completeness:**
- The offline-metstat crash the prior session fixed is related to but distinct
  from 2.1 (that fix catches exceptions; 2.1 is a normalization bug that should
  not raise in the first place).
- `parse_ttl("0d")` etc. from the prior session's report — unchanged (by
  design; see `docs/fix-plan-audit.md`).

---

## B. Misunderstanding verification (Section 1 of the report)

| # | Claim | Verified? | Evidence |
|---|-------|-----------|----------|
| 1.1 | ML facets are `blood plasma` / `blood serum`, not `Serum`/`Plasma` | ✅ live | `organismParts.term` with query `Homo sapiens`: `blood plasma`=202, `blood serum`=128, `Serum`=19, `serum`=31, `Plasma`=5, `plasma`=7. Using the capitalized variants collapses "human plasma" to ~5 studies. |
| 1.2 | WB `organisms` are Latin names; screen is substring-based | ✅ code | `build_candidates` puts the summary `species` (Latin: `Homo sapiens`) into `candidate.organisms`; live corpus has `Homo sapiens` and **no** `Human`. `scorer._check_terms` does `req in name` substring (case-insensitive) → `hard.organisms=["Human"]` drops every candidate. |
| 1.3 | WB SOURCE has no serum/plasma | ✅ live | Source values are `Blood` (plus `Bone marrow serum`, `Seminal plasma`, `Umbilical cord plasma`, `Infected Red Blood Cells`); no plain serum/plasma slot value. Serum/plasma is per-sample only (`Blood (plasma)` rows in /factors). |
| 1.4 | WB metabolite count ≠ matrix dim | ✅ | `/metabolites` rows have no abundance columns; datatable is a stub (see 2.4). ML `m_*.tsv` MAFs (parsed by `maf.py`) are a true per-sample matrix — prefer ML numbers when the matrix dimension matters. |
| 1.5 | API surface details | ✅ | `ScoreResult.ranked` (not `.scored`); `search_studies()` takes no `profile=` (use `profile_to_search_args`); `min_samples`/`min_raw_files` are client-side post-filters over at most `max_results` fetched hits (ordered by the API's default relevance, not score). Note: the loop paginates until it collects `max_results` *filtered* candidates or exhausts pages — the under-recall is the `max_results` cap + relevance ordering, not an early page stop. |

---

## C. Documentation plan (smooth out the misunderstandings — no code)

Target files: `SKILL.md`, `references/api.md`, and docstrings. All additions are
*new prose, no behavior change*.

### C1. SKILL.md — new subsection “Facets & vocabularies (read before searching)”
Insert after the Repositories section, before "Staged work":

- **MetaboLights `sample_types` are the index's `organismParts.term` facet
  values — case and wording matter.** Canonical values for biofluids (counts
  with query `Homo sapiens`, live, 2025-09): `blood plasma` (≈202), `blood
  serum` (≈128), `Serum` (19), `serum` (31), `Plasma` (5), `plasma` (7).
  `["Serum","Plasma"]` collapses the human-plasma universe to ~5 studies.
  Recipe: use the faceted variants (`blood plasma`, `blood serum`, …) often
  with `free_text=""`, and push disease/condition filtering client-side
  (title/abstract/descriptors) since facets don't carry it.
- **Workbench `organisms` are the Latin names** (`Homo sapiens`); the metstat
  SPECIES slot maps latin→common internally, and the shallow screen matches
  substrings, so `hard.organisms=["Human"]` silently drops every candidate.
  One shared profile serves both repos only for organisms written as Latin
  names.
- **Workbench SOURCE has no serum/plasma values** — the canonical source is
  `Blood`; serum/plasma must be verified per-sample from the factors payload
  after `inspect` (e.g. `sample_source = "Blood (plasma)"`).
- **Workbench disease terms are title-cased canonicals** (`Cancer`, `Lung
  cancer`, `Alzheimers disease`). Use exact canonicals (esp. `Cancer`) — a
  lowercase `cancer` in a profile is currently risky (see 2.2; fixed later,
  but exact canonicals are always safest).

### C2. SKILL.md “Limits to be honest about” — two additions
- `min_samples` / `min_raw_files` are **client-side post-filters** over at most
  `max_results` fetched hits, in the API's relevance order — raise `max_results`
  for full recall; don't assume the returned 200 are the "best 200".
- Workbench `metabolite_count` is the **identified-metabolite list length**, not
  a signal-matrix dimension; the m×s matrix lives in mwTab datatables, which the
  library does not parse yet. For “≥N metabolites as a matrix” requirements,
  prefer MetaboLights MAF numbers (`maf.py` parses the true per-sample matrix);
  quote WR numbers as “|metabolite list|, proxy for matrix rows”.

### C3. references/api.md — profile section table
Add to the `RequirementProfile` doc section: the facet table from C1, the
latin-name rule for WB organisms, the `Blood`-only source note, and the
metabolite-count proxy note.

### C4. Docstrings (module-level, no behavior change) that will carry the
knowledge next to the code
- `models.StudyRequirements.sample_types` / `organisms`: vocabulary-per-repo
  rules (ML facet values / WB Latin names).
- `searcher.search_studies` (`sample_type` param): cite the canonical facet
  values; already has `"blood plasma"` — expand with the full list + caveat.
- `scorer._check_terms` / `_shallow_hard_fail`: state the substring semantics
  and that serum-vs-plasma cannot be decided at screen time.
- `repositories/workbench/client.metstat`: note the single-match flat-record
  quirk (until fixed) and missing retry.
- `repositories/workbench/matcher.match`: state that exact-value equality on
  the normalized term is intended to win over fuzzy subtypes (documents the
  fix target for 2.2).
- `repositories/workbench/inspect._metabolite_count` / the datatable stub:
  clarify proxy semantics (list length, not matrix dims).

---

## D. Bug fix locations (for the later coding pass — not done now)

1. **2.1 flat record** — `client.metstat()`: after fetching, if the payload is
   a dict that itself carries a `"study"` key (single result), re-shape to
   `{"Row1": payload}` before returning. Also guard in `metstat_matches`
   (defensive: skip non-dict values).
2. **2.2 exact-value pre-pass** — `matcher.match()` before the fuzzy
   `process.extract`: normalize the term and, if any canonical value's
   normalized form equals it exactly, return that value (confident), no fuzzy
   needed. Alias table stays first (it bridges e.g. `alzheimers`→`Alzheimers
   disease`).
3. **2.3 retry/flakiness** — add a bounded retry (2–3 attempts, short backoff)
   around `client.get`/`metstat` for `RemoteProtocolError`/connect errors,
   mirroring the corpus-fetch pattern.
4. **2.4 datatable** — feature milestone (m×s matrix support), not a one-line
   fix; at minimum the docstring/C2 note holds until then.

## E. Suggested adherence test after the docs pass
- `sample_types=["blood plasma","blood serum","serum","plasma"]`,
  `free_text=""`, then client-side cancer filter on title/descriptors →
  recall in the hundreds, not ~5.
- WB profile `organisms=["Homo sapiens"]`, `sample_types=["Blood"]`,
  `diseases=["Cancer"]`, `min_samples=500` → survivors kept (no
  `organism (need ['Human'])` drops).
- Quote “metabolite count” as list-length for WR, matrix rows for ML MAF.