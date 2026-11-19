# Versioned data report correction

The existing `exploratory-analysis` skill now creates a saved report recipe and
updates all three dependent files together: JSON analysis/table, editable XLSX
data/chart, and editable DOCX report. It reuses the existing `data-v4` isolated
worker, file versions, skill runs, authorization and idempotency controls.

Supported scope: at most 100 rows, 12 columns and six numeric analysis columns;
one chart series with an explicit unit. Numeric fields contain finite numbers
or `null`. Zero stays a known value; missing cells remain missing. Arbitrary
older reports without a saved recipe are not reconstructable by this path.
User-declared cell corrections do not authenticate the external source fact.

In the project Skills workbench, open **Data analysis and linked report**, select
a saved CSV/XLSX source, enter category/value columns and unit, then create the
report. Select its result, choose a cell, enter its new value and rebuild. An
optional feedback checkbox links the correction to this task only. Old versions
remain available. Recompute uses the saved recipe without changing input cells.

## API contract

`POST /api/projects/{pid}/skill-runs` uses the existing run contract. An initial
run selects `exploratory-analysis` with `inputs.dataOperation` (`explore`) and
`inputs.dataReport={title,language,chart:{category,value,unit}}`. Select the
source using the existing `inputs.sourceRefs` and `dataOperation.sourceId`, or
provide an explicit typed table. Report input rejects unrelated operations,
filters and manual output-version claims.

A correction supplies `previousRunId` and
`inputs.dataRevision={expectedResultHash,edits:[{cell,expected,value}]}`.
The server supplies `expectedResultHash` in
`result.data_revision_available.result_hash`. Empty `edits` means recompute.
Optional `feedbackId` and `expectedFeedbackVersion` bind a currently authorized
tool feedback entry to the same parent run. Its proof covers recomputation and
saved artifacts; it does not create a lesson or train a model.

All three file heads and the optional feedback proof update in one transaction,
with expected versions checked again after worker execution. A failed write
cannot publish a partially updated successful bundle. A repeated request key
with the same payload resolves to the existing run; a different payload is a
conflict. Sources, ancestor runs and permissions are rechecked at execution,
publication and access to derived feedback/candidate content.

## Evidence, 16 November 2026

- Baseline missing report/correction fields: `reports/closure-20261116/data-revision/before.json`.
- Actual API/Docker evidence: `coverage-final.xml` and `live-coverage-final/` in
  the same directory: sum 30 → 40, known mean 10 → 40/3, three file IDs advance
  versions 1 → 2 → 3; originals remain byte-identical. A controlled SQLite write
  fault verifies atomic publication. This fault is not a production outage.
- LEARN-019 witness: the real tool route rejects the wrong `chart.value` input
  before any job; the report adapter maps its declared singular series to the
  existing worker's `chart.values` and verifies actual saved chart output.
  This is a new integration-contract repair witness, not a claimed historical
  connector outage.
- Final browser report: `ui/2026-11-16T16-57-26.476Z/report.json`, five passing
  Chrome/API/Docker cases: create, correct with feedback, download, reject bad
  correction, reopen on Arabic RTL mobile. Personal-memory gate/deletion checks
  are recorded separately in that report.
- **GAP-037, discovered during final verification:** cached tool feedback could
  survive upstream artifact revocation. `accepted-regression.xml` preserves the
  failing real API check (1 failed, 64 passed, 1 skipped). `learning.bind_target`
  now uses the complete run/source/ancestor check and validates current output
  access and bytes. `feedback-acl-fixed.xml` passed 59 tests, including actual
  API; `learning-boundaries.xml` passed 63 tests including direct revocation,
  ancestor revocation, own-output revocation, cycle and byte corruption. All
  affected feedback/candidate reads redact stale content without leaking causes.

The original closure inventory had 36 gaps; GAP-037 is an additional discovered
P0, not silently folded into the original denominator. Source hashes and support
limits are in `source-provenance-final.json` and `closure-scope.json`.

Run the API proof only with the explicitly configured local test worker:

```powershell
$env:RIWAQ_TEST_DATA_REVISION_LIVE='1'
$env:RIWAQ_DATA_REVISION_REPORT='reports/closure-new/data-api'
.venv/Scripts/python.exe -m pytest tests/test_skill_data_revision_live.py -q
```

The native Office GUI is not certified by these structural, cached-value and
browser-download checks. No automatic production deployment or training occurs.
