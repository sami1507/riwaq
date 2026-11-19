> الحالة الأحدث: المحرك **data-v4**. أضيف فحص كفاية التوقع وإتاحة الميزات
> والفصل بين المرصود والمتوقع مع رفض التعديلات غير المتسقة. التفاصيل والعقود
> والصورة النهائية وأوامر الاختبار في
> [دليل المتابعة](../reports/ai-core-continuation-20261116/data/NOTES.md).
> الأقسام التالية توثق دورة data-v2 التاريخية، ولا تُستخدم بصمت لإعداد العامل الحالي.

# AI core: structured data and numerical output evidence

Date: 2026-11-16. Existing `backend/skill_data.py` remains the only data engine. Its `execute_data` entry is called by the existing fixed sandbox worker and authenticated skill-run API. No new framework, model, library, external service, or database was introduced. Inputs remain bounded and scoped by the parent execution path.

## Changes

- Component version is `data-v2`. Explicit numeric cleaning recognizes Arabic-Indic and Persian digits only in the selected quantitative column. Decimal and grouping separators are explicit: decimal `.`, `,`, `٫`; grouping empty, comma, dot, space or `٬`. CSV import still preserves raw strings, leading zeros, empty strings and identifiers. There is no blanket inference from numeric-looking text.
- Conversion checks the original Decimal before converting to a JSON/XLSX number, verifies at most 15 significant digits and the existing magnitude bound, then checks round-trip value equality. A value that would round or underflow now rejects, or stays unchanged with a coordinate warning under explicit `on_error: keep`. XLSX reading retains an unrepresentable numeric value as raw text with a warning. Neither route silently turns nonzero data into zero.
- Statistics retain `nonnumeric_row_indices` and expose an explicit warning when quantities are excluded. Missing, zero and invalid text stay distinct. Weighted exclusions are announced. This does not guess what an invalid value means.
- Editable bar charts have an explicit zero-inclusive axis; negative values remain visible, and all-zero data gets a nondegenerate display range without an invented data point. Editing inputs updates caches and supported bar-axis bounds together.
- The chart contract adds optional `display_unit`: `units` (default), `thousands`, or `millions`. OOXML `c:dispUnits/c:builtInUnit` scales the displayed axis while source cells and caches retain full values. An unsupported value rejects before output. This is an explicit structured option, not inference from an arbitrary title in every language.
- `DataResult.engine_version` is serialized by the existing worker. The trusted parent `skill_transform.execute_transform` now requires this field to equal the host's `skill_data.VERSION` for the data domain before returning any result or artifact for publication. Missing, old or unknown values fail with `data_engine_version_mismatch`; transport success and a valid file hash alone are insufficient. The documents domain keeps its existing contract.

The existing Decimal statistics, joins, formula evaluator, CSV quoting, file writer and output re-reader were reused. No alternate calculator or chart-image engine was added.

## Evidence

- [data-baseline.json](../reports/ai-core-20261116/data-baseline.json): observed existing baseline command, 61 passed in 0.29s before code edits; source fingerprint is from the preserved baseline. This records console evidence, not invented JUnit.
- [data-before.xml](../reports/ai-core-20261116/data-before.xml): 23 new cases, 10 failed and 13 passed before implementation. Failures comprise four Arabic quantity cases, two precision-loss cases, numeric XLSX underflow, two absent explicit bar-baseline checks and unsupported display scaling. Unsupported features and defects are not conflated into a single quality percentage.
- [data-final-v2.xml](../reports/ai-core-20261116/data-final-v2.xml): 89 passed in 0.68s: 28 new cases and 61 existing cases. Independent reference results include weighted mean26, mean26.5/median2.5, join amplification6rows/40 and corrected total40 with mean40/3.
- [data-live-v2.xml](../reports/ai-core-20261116/data-live-v2.xml): 1 passed in 17.60s, with its original operation report preserved as [data-api-live-v2.json](../reports/ai-core-20261116/data-api-live-v2.json). The current [data-api-live.json](../reports/ai-core-20261116/data-api-live.json) repeats the 11 actual authenticated API/Docker operations on the final version-declaring image: synthetic data, scoped versions, stored/downloaded outputs, repeated-request IDs, cleanup/network receipts and expected rejection paths. The task sequence explicitly reruns dependent calculations; it does not prove automatic dependency invalidation for every conversation workflow.
- [data-corrected-chart.xlsx](../reports/ai-core-20261116/data-corrected-chart.xlsx): retained actual downloaded workbook. Region values are10 and30, with a missing unmatched group preserved. Full corrected order statistics remain4rows,3numeric,1missing,sum40,mean40/3. No native Excel visual check is claimed.

The initial API run is retained as `data-api-live-v1.json` / `data-live.xml`. A subsequent deployment review found that older installed images could still execute despite newer host code. Before the compatibility guard, [data-version-before.xml](../reports/ai-core-20261116/data-version-before.xml) had 5 failures, 2 passes and 1 explicit live skip. After the guard, [data-version-after.xml](../reports/ai-core-20261116/data-version-after.xml) has 121 passes and 3 explicit live skips, including data and document regressions. [data-version-live.xml](../reports/ai-core-20261116/data-version-live.xml) has 9 passes in 18.70s: seven local contract cases, a real old-image API rejection and the real current-image API workflow. Test groups overlap and must not be added as independent user journeys.

The actual legacy image `sha256:eafdd5e30b5d53602e2ef0163bb37b0f1dba48439cb913990575320369b847ad` produced a failed API run with `data_engine_version_mismatch` and zero saved artifacts; [data-old-image-blocked.json](../reports/ai-core-20261116/data-old-image-blocked.json) records this. Existing output/source files were not modified. The simulated parent-contract cases also reject a missing version even when `summary.version` says `data-v2`, and reject old, unknown and wrong-type version markers.

## Image and operation

The offline context builder reused existing verified wheels, fonts and base image; no downloads or installs. The final [data-image-build-v3.json](../reports/ai-core-20261116/data-image-build-v3.json) and [context manifest](../reports/ai-core-20261116/data-image-context-v3/manifest.json) record the exact bytes. Current candidate image, including the engine-version field:

`sha256:ba68c1064df0f850198b2d688014a571d4f82b73161daa37996be79c4ecbcb81`

The previous candidate `sha256:f68d2c2f93369e0bff4cf368677413d99b8a4a9fd0d47f0636336b43ee2ca3e2` and its `data-isolated-workers-v2.json` configuration remain retained for review. That image lacks the explicit result marker and is therefore also intentionally blocked by the updated parent. Its earlier live results do not grant current runtime compatibility.

The default BuildKit attempt misinterpreted a bare pinned image SHA as a registry name and failed before building, matching the documented prior build limitation. The final process used `DOCKER_BUILDKIT=0`, `--network none --pull=false`, and the existing local pinned base. The older image and configuration were retained. Only [the owned test configuration](../reports/ai-core-20261116/data-isolated-workers.json) was changed by this agent; production configuration was not changed. The deprecated legacy builder warning remains a tooling limitation.

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_ai_core_data.py tests/test_skill_batch03_data.py -q
$env:RIWAQ_TEST_AI_CORE_DATA_LIVE='1'
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_ai_core_data_live.py -q
$env:RIWAQ_TEST_AI_CORE_ENGINE_LIVE='1'
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_ai_core_engine_version.py tests/test_ai_core_data_live.py -q
```

The live commands need the already-built pinned images and the existing Windows Docker/DPAPI process access. They use fresh temporary databases and no production account or outbound message. `data-isolated-workers-pre-version.json` identifies the retained old test image; it is never a recommended active pin.

To enable the candidate in another authorized **test** instance, identify that instance's configured `RIWAQ_DATA_DIR`, stop its active work, preserve its existing `isolated-workers.json`, and change only `images.skill_transform` to the final candidate SHA above. Keep that instance's verified Docker executable/hash, daemon ID, endpoint and other image pins. Validate the installed local image and run the same API acceptance before considering wider activation. The owned test configuration is a concrete reference, not permission to overwrite another instance's configuration. No daily or production pin was changed by this work. An old configured data image now reports a blocking version error until explicitly updated; an instance with no transform pin remains unconfigured.

## Acceptance scope

- AI-05-T01/T02/T03/T04/T05: explicit CSV locale, structured scalar distinctions, typed Arabic quantities vs identifiers, binary rejection and quoted newlines are covered by actual engine tests.
- AI-11-T01/T02/T03/T04: reference statistics, cardinality enforcement, missing vs zero, and invalid quantity reporting are covered. AI-11-T05 covers explicit recalculation and saved output consistency, not automatic regeneration of all previously delivered outputs.
- AI-12-T01/T03: weighted means and retained outlier are computed deterministically. Causality, forecasting sufficiency and leakage detection were not implemented by these data changes.
- AI-13-T01/T02/T03: source cells, cached chart points, references and axis XML are checked. Native viewer layout and semantic title interpretation remain unverified; measured vs forecast styling is not added here.
- AI-17-T03: the existing table contract reserves row1 for headings; the equivalent A2=10, B2=A2*2 changes to15/30 with the formula retained. This is not a claim of arbitrary workbook-cell support at A1/B1.

The engine remains bounded to1000 rows/26 columns, charts200 rows, same-sheet supported formulas and documented formats. High-precision unsupported quantities remain explicit text or reject instead of silently losing digits. Existing privacy, permission, source-version and output-receipt checks remain with the parent execution path.

## References and licenses

No new dependency or license. Python standard-library Decimal/csv and the existing OOXML writer are reused. Native chart tags were checked against Microsoft's [DisplayUnits reference](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.drawing.charts.displayunits?view=openxml-3.0.1) and [Scaling reference](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.drawing.charts.scaling?view=openxml-3.0.1), documenting the Office2007/ISO29500 elements rather than adding the .NET SDK. Existing offline image licenses and notices remain in the context; no image redistribution or publication was performed.
