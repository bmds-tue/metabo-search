# metabo_search — cookbook (run-tested patterns)

Every pattern uses the one import surface: `from metabo_search import ...`.

The offline cells below (marked `# offline: …`) are **executed by
`tests/test_cookbook.py`** on every test run, so they can't rot: if a result
shape or constructor changes, the suite fails before any agent hits it.
Blocks without the marker need network / an LLM and are compile-checked only.

Real runs should go through a **cache root** (`.cache(".metabo_cache")`) —
warm steps replay from it, so iterating a recipe never re-hits the network.

---

## Pattern 1 — repo-specific filter + midstream continuation

A requirement like *"human serum/plasma"* is incommensurable across repos:
MetaboLights exposes it as a search facet (`"blood serum"` / `"blood plasma"`),
while the Workbench SOURCE vocabulary only has `"Blood"`. It can't be one
shared hard filter — **slice the survivors per repository between stages**,
then continue midstream. Any per-repo screen is just an expression over
survivor fields; results are plain dataclasses you construct by hand.

```python
# offline: exercised by tests/test_cookbook.py
from metabo_search import (FilterResult, InspectResult, OntologyTerm,
    RequirementProfile, StudyRequirements, StudyCandidate,
    pipeline, score)

profile = RequirementProfile(hard=StudyRequirements(organisms=["Homo sapiens"]))

def ml(tissue, sid):
    return StudyCandidate(study_id=sid, repository="metabolights",
                          organism_parts=[OntologyTerm(term=tissue)])

# Real life (network): f = quick_probe("serum", profile).cache(".metabo_cache").run()["filter"]
f = FilterResult(stage="shallow", survivors=[
    ml("blood serum", "MTBLS1"), ml("brain", "MTBLS2"), ml("blood plasma", "MTBLS3"),
    StudyCandidate(study_id="ST000001", repository="metabolomics_workbench",
                   organisms=[OntologyTerm(term="Homo sapiens")]),
])

# MetaboLights decides serum/plasma at the SHALLOW facet (no inspect needed):
serum_plasma = [c for c in f.survivors
                if c.repository == "metabolights" and any(
                    s in t.term.lower() for t in c.organism_parts
                    for s in ("serum", "plasma"))]

# The Workbench must decide SERUM-vs-PLASMA per sample, so it needs a DEEP
# pass.  inspect() is network-bound — this offline cell hands the deep
# candidates in exactly the shape inspect() would have produced:
deep = InspectResult(candidates=serum_plasma + [
    StudyCandidate(study_id="ST000001", repository="metabolomics_workbench",
                   organisms=[OntologyTerm(term="Homo sapiens")],
                   sample_metadata=[{"sample_source": "Blood (plasma)"},
                                    {"sample_source": "Blood"}],
                   sample_count=2, sample_file_parsed=True)])

keep = [c for c in deep.candidates
        if c.repository == "metabolights" or any(
            "plasma" in r.get("sample_source", "").lower()
            for r in c.sample_metadata)]

# stage="deep" marks the post-inspect carrier (a wrong stage is a plan-time
# error, not a silent bug).  Continue from ANY typed result via run(input=…):
final = FilterResult(survivors=keep, stage="deep")
rr = pipeline(score(profile)).run(input=final)

ids = {c.study_id for c in rr["score"].ranked}
assert ids == {"MTBLS1", "MTBLS3", "ST000001"}   # MTBLS2 (brain) dropped at screen
assert rr.score is rr["score"]                    # typed mapping: attr AND key
```

Short form (the shape you'll actually type):

```python
probe = quick_probe("serum", profile).cache(".metabo_cache")
f = probe.run()["filter"]                          # FilterResult

serum_plasma = [c for c in f.survivors
                if c.repository == "metabolights" and any(
                    "serum" in t.term.lower() or "plasma" in t.term.lower()
                    for t in c.organism_parts)]

deep = pipeline(inspect(workers=10)).run(
    input=FilterResult(survivors=serum_plasma, stage="shallow"))
rr2 = pipeline(score(profile)).run(input=FilterResult(
    survivors=deep.candidates, stage="deep"))
```

The same recipe composes `maf(...)`, `custom(...)`, and future predicates:
*slice → hand-build FilterResult → run(input=…)*.

---

## Pattern 2 — per-sample evidence after inspect (the honesty loop)

Neither repo's **search** data can claim serum vs plasma (ML facets give one
string; Workbench SOURCE has only `"Blood"`). The **per-sample rows** can — but
their keys differ by repository:

- MetaboLights rows mirror ISA columns: `Characteristics[Organism part]` is
  the tissue key.
- Workbench rows keep lowercase native keys (`sample_source`) plus ISA aliases
  (`Sample Name` / `Source Name` / `Factor Value[<label>]`).

```python
# offline: exercised by tests/test_cookbook.py
from metabo_search import StudyCandidate

c = StudyCandidate(study_id="MTBLS719")
c.sample_metadata = [
    {"Sample Name": "DCR00004_U",
     "Characteristics[Organism]": "Homo sapiens",
     "Characteristics[Organism part]": "blood plasma"},        # ← ML tissue key
    {"Sample Name": "S1", "sample_source": "Blood (plasma)"},  # ← WB source key
]

ml_row, wb_row = c.sample_metadata
assert ml_row["Characteristics[Organism part]"] == "blood plasma"
assert "plasma" in wb_row["sample_source"].lower()
```

---

## Pattern 3 — MAF is MetaboLights-only (do not hard-require it mixed)

Workbench candidates never ship `m_*.tsv` MAF files: `maf_files_parsed`
stays False and their `metabolite_count` is a `/metabolites` **list length**,
not a matrix. A hard `has_maf=True` / `min_metabolites` in a shared profile
therefore **silently drops the entire Workbench side**. This cell demonstrates
the foot-gun so you recognize it in a run — the guard is `databases=
("metabolights",)` for MAF needs, or MAF as `nice_to_have`.

```python
# offline: exercised by tests/test_cookbook.py
from metabo_search import (FilterResult, OntologyTerm, RequirementProfile,
    StudyCandidate, StudyRequirements, pipeline, score)

c = StudyCandidate(study_id="ST000001", repository="metabolomics_workbench",
                   organisms=[OntologyTerm(term="Homo sapiens")],
                   sample_metadata=[{}], sample_file_parsed=True)

rr = pipeline(score(RequirementProfile(
        hard=StudyRequirements(has_maf=True)))).run(
            input=FilterResult(survivors=[c], stage="deep"))

reasons = rr["score"].ranked[0].score.hard_fail_reasons
assert any("MAF" in r for r in reasons)          # the silent hard-drop
```

(If you only need a count, `analyze_maf_files(id, isa_dir=...)` reads the real
per-sample matrix from MetaboLights MAFs with zero LLM calls. And if a
pipeline already inspected the study, its MAFs are cached —
`analyze_maf_files(id, isa_dir=r["inspect"].isa_dirs[id])` is free, no
`download_maf_files` step needed. `min_metabolites` likewise does NOT
hard-fail a workbench study whose list the endpoint omitted —
`metabolite_list_unavailable` marks the count as unknown, not 0)

---

## Pattern 4 — result constructors for midstream continuation

Every result is a plain dataclass. `FilterResult` has exactly four fields
there is no `query=`/`profile=`), and `stage` is the one with meaning:

```python
# offline: exercised by tests/test_cookbook.py
from metabo_search import FilterResult, StudyCandidate, pipeline, score

shallow = FilterResult(
    survivors=[StudyCandidate(study_id="MTBLS1")],
    dropped=[(StudyCandidate(study_id="MTBLS0"), "no plasma facet")],
    order={}, stage="shallow")             # stage omitted → treated "shallow"
assert len(shallow.survivors) == 1 and shallow.stage == "shallow"

# stage gates which steps are legal next — a deep-continuation needs stage="deep"
deep_fr = FilterResult(survivors=[StudyCandidate(study_id="MTBLS1")], stage="deep")
wrong   = FilterResult(survivors=[StudyCandidate(study_id="MTBLS1")], stage="shallow")
pipeline(score()).run(input=deep_fr)       # OK: post-inspect carrier
try:
    pipeline(score()).run(input=wrong)     # plan-time error, raised before any work
    raise AssertionError("expected validation error")
except ValueError as e:
    assert "deep candidates" in str(e)
```

`ScoreResult` has **no `.dropped`**: it carries `ranked` + `table`, and fail
reasons are per-candidate at `sc.score.hard_fail_reasons` (plus
`sc.score.per_criterion` / `criterion_explanations`). Batch dropped-reasons
live in the shallow `FilterResult.dropped` (or legacy `report.screening.dropped`).

---

## Pattern 5 — end-to-end: discover → sentences → download → manifest

Network + one LLM call per study (compile-checked here; run with a cache root):

```python
from metabo_search import (RequirementProfile, StudyRequirements,
    quick_probe, full_report, download, export)

profile = RequirementProfile(
    hard=StudyRequirements(organisms=["Homo sapiens"], min_samples=50,
                           sample_types=["blood plasma"]),
    nice_to_have=StudyRequirements(techniques=["LC-MS"], has_maf=True),
)

# cheap first — narrow the profile before spending inspect budget:
probe = quick_probe("lipidomics", profile).cache(".metabo_cache")
print(probe.run()["filter"].fmt())                 # no inspect, warm replay

p = (full_report("lipidomics", profile, top=3).cache(".metabo_cache")
       .extend(download(categories=["raw"], dest_dir="./data"))   # needs ≥1 constraint
       .extend(export(path="manifest.csv")))
r = p.run(llm=call_llm)      # call_llm(prompt) = you; one recipe call/study
print(r["describe"].fmt(detail=True))
print(r["download"], r["export"].rows)
```

Recipe details: `describe` caches per study, per `revision`; re-roll wording
via `revise_samples(task, new_text)` — old wording is preserved for comparison.

---

## Pattern 6 — deep-inspect safely (workers, thread parsing, straggler retry)

Too many workers (~16+) can overload the remote file server: studies come
back **shallow** (soft-fail, no crash — the pipeline refuses to cache such a
degraded inspect, so re-runs re-inspect rather than replay stale data, but
the re-run itself costs time you can avoid). Three habits: moderate per-repo
workers (`≈8`), thread parsing in ad-hoc scripts (`parse_workers=0` — also
skips the spawn/re-import foot-gun), and a **serial retry loop for
stragglers** that heals THIS run before you trust any of its numbers.

```python
# offline: exercised by tests/test_cookbook.py
# Detecting stragglers is pure logic — works on any InspectResult:
from metabo_search import InspectResult, StudyCandidate

def stragglers(res: InspectResult):
    return [c for c in res.candidates if c.inspection_depth != "deep"]

ok   = StudyCandidate(study_id="MTBLS1", investigation_file_parsed=True,
                      sample_file_parsed=True)   # healthily deep
lost = StudyCandidate(study_id="MTBLS2")          # failed download: stays shallow
deep = InspectResult(candidates=[ok, lost], isa_dirs={"MTBLS1": "/x"})
assert [c.study_id for c in stragglers(deep)] == ["MTBLS2"]
```

```python
# The real loop (network; compile-checked).  deep_inspect wraps the step:
#   pipeline(inspect(workers=…, parse_workers=0)).cache(".metabo_cache") \
#       .run(input=FilterResult(survivors=…, stage="shallow"), force=…)["inspect"]

ml_deep = deep_inspect(ml_cands, workers=8, force=True)   # moderate, per repo
wb_deep = deep_inspect(wb_cands, workers=8, force=True)
deep = InspectResult(
    candidates=ml_deep.candidates + wb_deep.candidates,
    isa_dirs={**ml_deep.isa_dirs, **wb_deep.isa_dirs})

bad = stragglers(deep)
for attempt in range(3):
    if not bad:
        break
    time.sleep(30)                                   # let the server recover
    retry = deep_inspect(bad, workers=1, force=True)  # serial + cache bypass
    fixed = {c.study_id: c for c in retry.candidates}
    deep = InspectResult(
        candidates=[fixed.get(c.study_id, c) for c in deep.candidates],
        isa_dirs={**deep.isa_dirs, **retry.isa_dirs})
    bad = stragglers(deep)
```

`force=True` on the retry defeats any stale cache entry during recovery; the
pipeline already refuses to cache a degraded inspect, so a clean deep pass
replays warm and a failed one re-inspects on the next run.