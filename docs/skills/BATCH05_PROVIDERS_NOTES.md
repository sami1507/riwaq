# Batch 05 provider controls — 2026-11-16

## Scope and evidence

Reused the existing Ollama adapter, Guard, generation stream, grounded-message builder, output contracts, session identity and project/task boundaries. No dependency, model download, paid service, cloud enablement or alternate secret store was added. Existing cloud generation remains quarantined. Code: `backend/providers.py`, `provider_policy.py`, `provider_runtime.py`, `context_budget.py`, `grounding.py`, `output_contracts.py`. Tests: `tests/test_skill_batch05_providers.py`, `test_skill_batch05_provider_regressions.py`.

Windows 11, Python 3.12.10, HTTPX 0.28.1. Before-change baseline: **63 passed**, `reports/skills-batch-05-20261116/providers-baseline.xml`. Deliberate acceptance replay against the saved unmodified baseline: **three failed**, `providers-defects-before.xml`: string `done: "false"` accepted as completion; an early required conversation constraint silently pruned; invalid structured JSON streamed before validation. The same assertions pass after the fixes. Final combined run: **105 passed in 12.74 s**, including 42 new and 63 previous tests, `providers-combined-final.xml`. These are deterministic local/API/MockTransport tests, not proof of live cloud interoperability. Runs used Windows execution with loopback permission because sandboxed Windows asyncio initialization could not open its local event-loop connection.

One live bounded local check is separately recorded in `provider-live.json`: Ollama **0.40.16**, installed `qwen3:4b-instruct`, fixed synthetic JSON `{ "value": 7 }`, 64 output-token cap, 45-second deadline, no user document or cloud call. The fixed text/structured-output probe passed; command wall time 2.778 s. It does not measure language accuracy, vision, tools, speech, grounding or alternate-model equivalence. Current metadata declares text/tools/structured protocol support and no image support. Measured state is distinct, bound to model digest/provider version/capability fingerprint, expires after one hour and is unknown after process restart.

## Capability boundaries

| Number | Control and actual path | Verified scope / remaining limits |
|---|---|---|
|107|Fresh `/api/show`, installed `/api/tags` identity and `/api/version`; generation checks required image/text/context capabilities before execution. No name-based vision guess.|Local declared metadata and one fixed text/JSON probe. General language/model quality and vision/tools/audio remain unverified or unsupported.|
|108|Grounding retains complete previous turns, current request and instruction constraints; byte-based estimate, 512-template reserve, output reserve, explicit overflow error; selection of whole source blocks with coverage receipt. Adapter enforces budget again and disables truncation/shift.|Conservative approximate count, explicitly labelled. No exact tokenizer or automatic semantic compaction. Large history is rejected for explicit scope adjustment, not silently shortened.|
|109|Strict bounded JSON parse/schema validation rejects duplicate keys, nonfinite numbers, extra fields, wrong types and semantic order/resource checks when supplied by trusted caller. Structured stream buffers until normal completion and validation.|Local stream and authenticated API/SQLite test: invalid JSON produces no delta and no saved answer. Valid schema does not prove factual truth or authorization. No automatic fabrication/repair of required fields.|
|110|Explicit local fallback list (default empty, max three); checks privacy, capability, output state, known error, time and attempt budget. Actual generation path retries another selected installed local model only on eligible pre-output transient failures.|MockTransport proved selection/refusal. No cloud fallback; no actual live alternate-model failure injected. Unknown writes and possible partial output are not replayed. Quality equivalence is unmeasured.|
|111|Shared bounded retry classifier, exponential jitter, Retry-After, deadline/cancellation, metadata-only attempt receipts. Used by metadata calls and generation.|Fake-clock cancellation/deadline/limits and actual adapter mock flows. Read/write timeout with unknown effect is not blindly retried. No external-write retries claimed.|
|115|Process-local admission: capacity 2, one active per owner, bounded queues, FIFO among eligible owners, minimum spacing and shared Retry-After cooldown. Applied to generation.|Application resource bounds, **not invented provider quotas**. Simulated concurrent fairness/cooldown and cancellation tested. Multiple application processes do not share this limiter.|
|116|Read-only fresh local health plus optional explicit fixed synthetic probe; model identity/fingerprint invalidates measured cache. API rechecks trusted project membership around result.|Local operation-specific health only. General connection success is not permission to write/send. Cloud/other service health remains outside this adapter.|

## Reproduction

From the project root:

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m pytest -q tests/test_skill_batch05_providers.py tests/test_skill_batch05_provider_regressions.py tests/test_generation_reliability.py tests/test_grounding_context.py tests/test_output_contracts.py tests/test_guard_policy.py
```

The regression file uses the existing current source by default. `RIWAQ_BATCH05_PROVIDER_BEFORE=1` selects the saved baseline only for the three documented defect demonstrations; those failures are intentional evidence, never a passing release gate. See the test files and XML manifests for the exact case identifiers. No CI step should set that flag for normal acceptance.

The product runtime panel uses authenticated `GET /api/projects/{pid}/skill-runtime`; explicit local probe uses `POST .../probe`. No arbitrary prompt/document can enter the fixed probe. `GET /api/projects/{pid}/skill-runs/{rid}/audit` is a separate scoped metadata view. Runtime inspection tasks record results through the existing skill-run lifecycle; inspection is not certification of the described subsystem.

## Official references and licenses

- Existing HTTPX 0.28.1 (BSD-3-Clause), [timeout documentation](https://www.python-httpx.org/advanced/timeouts/), [exception taxonomy](https://www.python-httpx.org/exceptions/).
- Ollama 0.40.16 (MIT service), version-matched [API request/response structures](https://raw.githubusercontent.com/ollama/ollama/v0.40.16/api/types.go), [chat endpoint](https://docs.ollama.com/api/chat), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs). `truncate`, `shift`, `think`, format schema and capability metadata were checked against the actual version source. No model weights were redistributed; the preinstalled model license was not replaced or expanded by this work.
- Python standard library (PSF license), existing Pydantic runtime schema path, no new package or font.

## Known limits / resume

A process-wide application limiter is not a distributed provider-quota service. Local estimates deliberately overcount; they are not exact billed token counts. Unknown outcomes remain unknown. Only fresh declared metadata and explicitly recorded probes appear as evidence; no ability is inferred from a model name. Live cloud use, native desktop post-pairing and general multilingual semantic evaluation remain untested. Run a separate authorized evaluation with the intended model and representative nonprivate fixtures before claiming those scopes.


## Product UI verification

Added `frontend/src/SkillRuntimePanel.tsx`, expanded `SkillsWorkbench.tsx` and the existing `skills-api.ts` client, and reused `skills.css`. No framework or charting dependency was added. Catalog UI consumes the same server fields for installed/enabled/ready/tested, lists concrete blocker codes and disables preparation only when installation or enablement is false. An enabled capability with missing inputs exposes Prepare requirements while retaining its blockers and runtime checks. System entries submit typed `systemOperation` to the existing lifecycle. The runtime view reads actual scoped storage/worker/queue/timing/model/release evidence; the explicit probe button sends only the fixed server-owned synthetic request. Audit reads only the selected authorized task. Requests abort on unmount/project change and stale responses are ignored. Failed refresh clears previous readiness. A snapshot older than 60 seconds or without a valid timestamp is labelled stale; worker readiness also ages according to the server's heartbeat threshold. Missing timing is unavailable, never an invented zero.

React checklist applied: effect cleanup and AbortController, project-keyed parent state, explicit loading/error/empty states, accessible button/field labels, RTL, existing visual styles and no CSS framework migration. `npm run build` passed with TypeScript 5.9.3 and Vite 7.3.8. Existing main-bundle size warning remains (approximately 695 kB); no build error.

Final real browser acceptance: **9 actual UI/API/storage cases + 3 explicitly simulated response-fault cases passed**, Chrome **157.0.8103.52** on Windows; English desktop 1440×1000 and Arabic RTL mobile viewport 390×844. Exact report and source hashes: `reports/skills-batch-05-20261116/ui/2026-11-16T11-39-51.209Z/report.json`. Zero page errors and zero external browser requests. Screenshots in the same directory: `runtime-en.png`, `local-probe-en.png`, `runtime-ar-mobile.png`; all three were visually inspected (layout readable, no tested horizontal overflow). This tests the web UI running on Windows, **not** the native desktop application. Native post-pairing remains deferred at the user's request.

Actual cases: 125 unique catalog entries agreeing with server readiness and a concrete disabled entry; enabled but unready PDF reading can prepare its required inputs without claiming readiness; system task through catalog and pinned versions; runtime storage/worker/queue/model/release readback; scoped audit without original request text; explicit local fixed-JSON probe; structured integer zero validation; recovery after a removed fault fixture; Arabic RTL mobile. Response fixtures separately tested stale worker/unknown queue/missing timing, an old nominally-ready snapshot, and revoked project access clearing the prior snapshot. The release state observed was `unverified`, accurately displayed; this run does not establish a release approval.

The first UI attempt was correctly rejected because the isolated service origin had not been added to its explicit development allowlist. Its test-service launch was corrected with `RIWAQ_DEV_ORIGIN=http://127.0.0.1:8770`; no authentication bypass was added. A subsequent script attempt selected the collapsed/inert mobile sidebar control; the script was corrected to use the visible composer entry. Those incomplete reports are retained and are not counted as passes. A review also caught that disabling every unready card prevented preparing file-reading inputs; the button now separates preparation from activation, and a real UI regression case covers it. Final acceptance was rerun after the stale-snapshot and preparation fixes; the above report is authoritative.

Reproduce only against a separately started synthetic service:

```powershell
$env:RIWAQ_DATA_DIR = Join-Path (Get-Location).Path 'reports/skills-batch-05-20261116/ui-service-data'
$env:RIWAQ_DEV_ORIGIN = 'http://127.0.0.1:8770'
# In a separate terminal, only when port 8770 is unused:
.\.venv\Scripts\python.exe -X utf8 -B -m uvicorn backend.app:app --host 127.0.0.1 --port 8770 --no-access-log --no-proxy-headers
# Test terminal, with the same RIWAQ_DATA_DIR:
$env:RIWAQ_URL = 'http://127.0.0.1:8770'
node frontend/scripts/skills-batch05-ui.mjs
```

The test pairs its own isolated Chrome context with that synthetic database, creates synthetic projects/tasks, performs one bounded local probe per complete run, then closes that browser. It refuses other URL/data-directory combinations. It does not print pairing codes, access production accounts, send email, create cloud events, or claim delivery. The standalone live probe and browser probe are separate invocations, not independent model-quality benchmarks.
