# CI — offline gate + live conformance

Two tiers, with one rule: **a pull request is fast and fully offline; the real
network runs separately and never blocks a review.**

| Workflow | Job | Trigger | Runtime | Network |
|---|---|---|---|---|
| `ci.yml` | `test` (3.11/3.12/3.13) | push to `main`, every PR | ~1–2 min | no |
| `ci.yml` | `package` | push to `main`, every PR | ~1 min | no |
| `live.yml` | `live` (fast) | nightly, push to `main`, manual | ~40 s | yes |
| `live.yml` | `live-slow` | weekly (Sun), manual | ~95 s | yes |

## Offline (`ci.yml`)

- `test`: matrix on 3.11/3.12/3.13 → `ruff`, `pyright` (strict), `pytest`.
- `package`: `uv build`, `twine check`, then install the **wheel** twice — once
  plain (assert `import metabo_search` works with no `duckdb`/`pyarrow`) and once
  with `[ingest]` (assert `Workspace` resolves). This guards the optional
  dependency boundary from regressing in a real install.

The live tests are deselected by default via `addopts = "-m 'not live'"`, so a
plain `pytest` is always offline.

## Live (`live.yml`)

- `live` (fast, nightly): the full promise matrix below, `workers=1`, no corpus.
- `live-slow` (weekly): adds the corpus cache test — the `/ST/summary` fetch is
  ~90 s, so it does not run every night.

The fast job runs on push to `main`; both run on `workflow_dispatch`.

### The pinned sample

Published studies, asserted loosely so a curation event reads as an explicit
"anchor changed" failure rather than a mysterious assertion error.

| Study | Shape | What it exercises |
|---|---|---|
| `MTBLS1` | 132 samples / 220 metabolites, abundances for all | round-trip fidelity |
| `MTBLS10` | 30 samples / 1496 metabolites, `mz`+`rt` set | numeric MAF columns |
| `MTBLS744` | 4 inspected samples → 2 natural keys, 62 metabolites | variant-sheet dedupe + duplicate reporting |
| `MTBLS14005` | `study()` works, `inspect()` errors | best-effort error path |
| `ST000001` | 24 samples / 1 analysis / 102 metabolites | round-trip fidelity |
| `ST000002` | 12 / 1 / 142 | not a singleton |
| `ST000004` | 1 sample / 8 analyses / 540 metabolites | analysis cardinality |
| `ST004741` | 18476 samples, `/metabolites` → `[]` | "empty means not listed" |
| `ST999999` | missing | `NotFound` / skip |

Plus one dynamic test: `ml.search(min_samples=N)` must return only studies with
`sample_count >= N` and unique ids.

### Promises asserted (single source: `tests/contract.py`)

Both the offline and live suites call these, so a promise has one definition.
The live job checks that upstream data still honours it.

| Promise | Contract function |
|---|---|
| Rows round-trip on their own natural keys | `assert_*_detail_round_trips` |
| Collapsed keys are reported, never thrown | `assert_duplicates_reported` |
| Re-ingest is idempotent (audit excluded) | `assert_idempotent` |
| Enrichment never prunes (upsert-only) | `assert_no_table_shrinks` |
| One audit row per written table | `assert_audit_trail` |
| Errored details are skipped | `assert_skipped` |
| `ml_*` / `wb_*` never unify | `assert_cross_source_isolated` |

Non-contract live checks cover request counts (`wb.inspect` with a passed
summary = 1 request samples-only, 3 full), best-effort ordering, and the corpus
cache hit.

## Running locally

```bash
.venv-local/bin/python -m pytest -q                                          # offline
.venv-local/bin/python -m pytest tests/live -m "live and not live_slow" -q   # live fast
.venv-local/bin/python -m pytest tests/live -m live_slow -q                  # + corpus (~95 s)
```

## Adding a promise

1. Write it once in `tests/contract.py`.
2. Call it from an offline test (fixtures / `MockTransport`).
3. Call it from a live test on the pinned sample (or add an anchor).

## Reliability rules

- Assert invariants and non-emptiness, not upstream counts that curation can
  change.
- Tolerate transient 5xx: the live fixtures retry patiently, and `transient` in
  `client.stats` is expected to be non-zero.
- Snapshot `client.stats` immediately around the call under test.
- Never `inspect()` `ST004741` — 18476 samples; call `/metabolites` only.
