> متابعة أحدث: أضيف تصحيح واحد محدود بعد رفض الإجابة، ثم استرداد اقتباسات
> مطابقـة بوصفها نتيجة جزئية عند استمرار التعثر. التحقق لم يُضعف. العينة الجديدة
> بعد تجميد التعديل حققت2/3معايير تصحيح، مع إبقاء الثالثة جزئية.
> [الحدود والميزانية والأدلة](../reports/ai-core-continuation-20261116/evidence/NOTES.md).
> النتائج السابقة أدناه تاريخية؛ لا يُعاد تصنيفها نجاحًا بسبب التعديل.

# AI core — evidence and grounded conversation verification (2026-11-16)

This note records implementation and bounded evidence, not certification of all100 user cases or general semantic accuracy. The repository baseline is `reports/ai-core-20261116/baseline-manifest.json` (663 files, captured before this task; no Git repository). Earlier user changes were preserved. No new dependency, model download, cloud provider, production data or outbound document sharing was introduced.

## What changed

- `backend/research_answer.py`: validates the existing bounded JSON envelope through the actual provider contract, rejects duplicate JSON keys, binds claim numbers to exact cited quotes rather than all text or a version label, rejects a narrow same-script lexical mismatch, and checks recognized adjacent number/unit pairs in each claim's exact quoted evidence. These checks also apply to inferences. Model output remains buffered until successful terminal status and authorization recheck. The receipt explicitly says semantic support is not certified.
- `backend/grounding.py`: rejects explicit whole-table aggregates over retrieved excerpts before invoking the model; records selected and omitted resource identities; gives every candidate resource/version an opportunity to fit before a second excerpt from the same resource. It preserves complete authorized conversation turns or returns an explicit budget error. This does not prove the retriever found every needed document. In structured evidence mode, records carry integer source identifiers; the contradictory instruction to place rendered citation markers inside the internal JSON is removed. The server still renders final citations.
- `backend/app.py`: ordinary local conversations using files/knowledge now use the same source validator with their authorized history, project instructions, user memory, request constraints, existing deadline/cancellation and context budget. Source authorization is checked before and after generation. Explicit structured-output/reasoning modes retain their existing specialized paths and are not silently certified by this new validator. No-source behavior is preserved. No second retrieval engine was added. The concurrent problem-calculator integration in this file belongs to the registry agent.
- `backend/research.py`: old private citations without an original hash binding are no longer called current. A changed source hash, even with the same quoted text, invalidates a bound private citation. Public old versions remain explicitly historical; revoked sources remain inaccessible. Fetch/check time is not an event or publication date. Missing hashes no longer create false duplicate-source notices. Equal hashes identify exact copies; different hashes or publishers do not certify independence.
- `backend/output_contracts.py`: raw JSON traversal ceiling changed from8 to12 so the existing logical schema depth5 can include its `properties`/`items` wrappers. Logical depth6 remains rejected. Trusted acyclic Pydantic research schemas are lowered to the existing provider subset; arbitrary model `$ref` definitions were not enabled.

No new storage migration is required. Research receipts are stored using the existing message metadata and source binding paths. The stricter validator may return evidence-only output where an older version rendered an unchecked answer. Legacy source links can still show an authorized exact excerpt, but no longer assert its version is current without a binding.

## Before/after evidence

All reports are under `reports/ai-core-20261116/`.

| Report | Actual result | Meaning |
|---|---|---|
| `evidence-baseline.xml` |87passed|Existing research, retrieval and grounding baseline before these changes.|
| `evidence-defects-before.xml` |11failed,3passed|Reproduced quote20/claim200, duplicate JSON key, incomplete aggregate and source coverage faults.|
| `evidence-provider-contract-before.xml` |1failed|The mocked research path hid a real unsupported schema boundary.|
| `evidence-binding-before.xml` |4failed,4passed|Reproduced private-version binding and false corroboration faults.|
| `evidence-unit-defect-before.xml` |5failed,4passed|A real live response revealed200units backed by200items; the fixed tests also cover kg/meters, Arabic, Hebrew and another uncited sentence.|
| `evidence-unit-defect-after.xml` |9passed|Known numeric-unit swaps rejected; genuine measurements and explicit aliases accepted.|
| `evidence-final.xml` |175passed in9.02s|Final affected evidence, actual isolated API/SQLite, source permissions, context, schemas and provider contracts. Model output is simulated in these API regression cases.|
| `evidence-chat-compatibility.xml` |72passed,17skipped|Earlier broader chat/facts/knowledge/document compatibility run. Optional parser/Docker cases were skipped, not counted as success. The final175-case run covers subsequent evidence changes.|

Intermediate failed/working reports are retained. In particular, `evidence-final-unit-bound.xml` records171passed and4 legacy receipt wording assertions; the wording was restored without weakening behavior, then the final175 cases passed. No outcome from these component tests means a UI flow or real cloud provider was tested.

Useful regression nodes:

- `tests/test_ai_core_evidence.py::test_cited_twenty_cannot_support_two_hundred_elsewhere`
- `tests/test_ai_core_evidence.py::test_known_unit_cannot_be_swapped_even_in_inference`
- `tests/test_ai_core_evidence.py::test_known_unit_cannot_be_borrowed_from_another_number_or_uncited_sentence`
- `tests/test_ai_core_evidence.py::test_known_unit_reference_accepts_genuine_measurements`
- `tests/test_ai_core_evidence.py::test_full_table_aggregate_rejects_partial_excerpts_before_model`
- `tests/test_ai_core_evidence.py::test_budget_coverage_preserves_each_candidate_document_before_second_excerpt`
- `tests/test_ai_core_evidence.py::test_research_revocation_after_model_blocks_answer_publication`
- `tests/test_ai_core_evidence_api.py`: actual local evidence readback, versions and permission cases.
- `tests/test_ai_core_grounded_chat.py::test_ordinary_grounded_rejects_known_unit_swap_before_stream_and_storage`
- `tests/test_ai_core_grounded_chat.py::test_ordinary_grounded_mixed_public_private_retains_scope_and_exact_source_ids`

Reproduce the final local contract suite on this Windows environment:

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest -q tests/test_ai_core_evidence.py tests/test_ai_core_evidence_api.py tests/test_ai_core_grounded_chat.py tests/test_research_answer.py tests/test_research_chat.py tests/test_research_security.py tests/test_grounding_context.py tests/test_skill_retrieval.py tests/test_skill_retrieval_api.py tests/test_output_contracts.py tests/test_skill_batch05_providers.py --tb=short
```

## Actual local model and API checks

Final report: `evidence-local-live-unit-bound.json`; actual Ollama0.40.16, installed `qwen3:4b-instruct`, model digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`. Five local generation calls and one zero-call aggregate guard. Component calls use context8192/output512/temperature0/seed19. Ordinary API calls use isolated fresh app settings context8192/output1024, adapter default temperature0.3, no fixed seed. They are not the same conditions, and provider nondeterminism cannot be attributed to a code improvement alone. The report records source hashes, model metadata and individual receipts; no secret values or hidden reasoning are included.

| Fixed case | Result | Observed output |
|---|---|---|
| Direct supported research fact |Pass|20units with exact quoted evidence.|
| Research draft200 corrected against source20 |Pass|Corrected20units.|
| Arabic research answer |Pass|20وحدة with exact English quote; this is one multilingual sample, not a translation-quality benchmark.|
| Aggregate over complete table from excerpts |Pass|No model call; explicit incomplete-scope explanation.|
| Ordinary authenticated chat correcting draft200 |Quality criterion failed, unsupported output withheld|The model added an unnecessary inference linking200units to200items. Final validator rejects `number_unit_without_evidence_provenance`; only clearly labeled evidence excerpts are shown and persisted.|
| Independent varied ordinary chat, separate project/file |Pass|17.5kilograms supported load, not3kilograms packaging; actual API response and stored content agree.|

Thus5of6 fixed quality checks passed. The failed correction is not advertised as completed. It must not be hidden by the successful correction in the separate research-component path.

An earlier run, `evidence-local-live-varied.json`, **did render** the unsupported number/unit inference before the unit-binding fix. An intermediate progress description incorrectly called all such failures safe rejections; reading the actual output corrected that diagnosis. The old response is retained as defect evidence. `research_answer-before-unit-fix.py` preserves the affected implementation, and the before/after tests plus final live receipt show the fix. Other initial reports (`evidence-local-live-final.json`, `...corrected.json`, `...integrated.json`, `...diagnostic.json`, `...records.json`) document format/provenance failures rather than successful completed answers. None were replaced with selected successful retries.

Run the fixed synthetic probe only with the existing local model; it refuses overwriting prior reports and creates a separate data directory for each run:

```powershell
$env:RIWAQ_RUN_LOCAL_EVIDENCE_PROBE='1'
.venv/Scripts/python.exe -X utf8 -B scripts/probe_ai_core_evidence.py reports/ai-core-20261116/evidence-new-local-run.json
```

A nonzero exit is expected while any quality criterion fails. The script does not call a cloud fallback. Do not publish the isolated service data directories or their authentication material. The JSON report contains only the explicitly synthetic provider output and receipts.

## Limits and next step

- Exact quote/number checks and recognized adjacent units do not prove full entailment, entity identity, causality, negation, complete retrieval, conversion arithmetic, or freshness. The lexical mismatch check can reject same-script synonyms and does not validate cross-script meaning. Numeric-unit aliases cover a limited set of English/Arabic/Hebrew count, mass, distance and time units. Unknown units, nonadjacent wording, and all other languages remain semantically uncertified; the product remains multilingual.
- No raw excerpt has a trusted full-source coverage receipt. Whole-table totals remain blocked in this path; use the existing typed full-table operations. Explicit aggregation detection is a bounded multilingual pattern guard, not universal intent recognition.
- A distinct source URL/publisher/hash is not a proof of independent reporting. Publication, source-version verification, and event time remain separate fields; event time is not fabricated. No new live original-source search benchmark was performed.
- This task tested actual Windows Python, local API/SQLite and Ollama. It did not test the native post-pair UI, which the user left pending, or a cloud model/browser/live web source.
- Next: preserve the rejecting contract while improving the local model's ordinary correction quality under a separately versioned development sample, then rerun an independent varied evaluation set. Do not relax the exact quote/unit requirement to turn the current failure green. Root's100-case evaluation must keep broader semantic cases partial unless their own evidence supports more.

## Follow-up: bind an ordered resource to actual execution

A read-only review found that `request_record.resolved_resource` could name B while the worker still read both A and B. Two actual API/Word inspections reproduced that defect before the fix (`resource-binding-before.xml`:2failed); A contained111units and B222units.

`backend/skills.py` now persists a server-created `execution_source_binding` for supported single-file reading, summary, translation and text exports. It preserves the original submitted payload and idempotency hash, narrows the active source snapshots and effective worker input to B, and validates that binding again on execution/resume. Genuinely inherited lineage retains its authorization obligations. Unsupported multiple-source operations and explicit conflicting artifact/operation inputs return a clarification before starting a tool; they are not silently narrowed. Preview and execution also expose the same merged requested-output metadata, added only after executor selection so routing behavior remains unchanged.

`tests/test_ai_core_resource_binding.py` adds12actual local cases, including saved Word content inspection, same-key repeat, source revision/permission change, a change during summary, unrelated A revocation, and a supported checkpoint resume before summary dispatch. Translation uses a labelled simulated provider; it proves selected input and export binding, not live translation quality. Final affected suite: `resource-binding-final-v2.xml`,125passed,1skipped in26.27s. `resource-binding-final.xml` retains the earlier124passed/1failed/1skipped result: that test incorrectly expected automatic replay of an already-running summary. The final test expires after the committed read and before summary dispatch, matching the existing safe replay policy; the policy was not expanded.

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pytest -q tests/test_ai_core_resource_binding.py tests/test_ai_core_request.py tests/test_ai_core_recovery.py tests/test_skills.py tests/test_skill_batch02_api.py tests/test_skill_batch02_content.py tests/test_skill_batch03_data_api.py tests/test_skill_batch05_execution.py --tb=short
```

This does not establish universal pronoun resolution or arbitrary document editing. Resource identity still comes from authorized input ordering, never a filename guess. Existing typed multi-resource operations require their own explicit matching sources.
The skipped regression was test_real_csv_clean_stats_join_aggregate_chart_and_scope: opt-in existing pinned Docker worker was not enabled for this run; no worker image was downloaded.
