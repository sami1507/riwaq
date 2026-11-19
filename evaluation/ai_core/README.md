# AI-core evaluation

`AI_CORE_EVAL_CASES.json` contains exactly100 ordered user cases from
`AI-01-T01` through `AI-20-T05`. Their expected outcomes are requirements, not
generated answers. `user-criteria-reference.md` is a bundled faithful condensed
reference, with hash/line attribution in each case. The current user request is
available; this reference is summarized, not verbatim. Unexecuted subcriteria
remain unproved even when a related component test passes.

The user cases are known to developers. `independent-sample.json` is a separately
authored12-case deterministic sample, frozen before this implementation in
`independent-sample.freeze.json`. It is visible engineering data, not a blind
holdout or human-certified semantic benchmark. Its baseline ran imports from the
saved baseline tree, not today's modules, and is recorded separately under
`reports/ai-core-20261116/independent-baseline.json`. No measured improvement is
claimed merely because the same sample continues to pass.

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_ai_core_evaluation.py -q
.venv/Scripts/python.exe scripts/run_ai_core_evaluation.py --output reports/ai-core-20261116/new-evaluation
.venv/Scripts/python.exe scripts/run_skill_regression.py --ci --ai-core --no-seal --output reports/ai-core-20261116/new-ci
```

The runner accepts only a **new directory below reports/**. It does not accept
an XML report, arbitrary shell command, model response, provider secret, or
production data directory as evidence. It starts a fixed reviewed selection of
existing/new pytest tests with a temporary `RIWAQ_DATA_DIR`, reads only the fresh
JUnit result it created, and evaluates the frozen independent component sample.
No deployment, SMTP delivery, cloud inference, personal memory activation or
release approval is performed. Windows DPAPI test authentication may require the
same real Windows identity used to run the repository's existing tests.

The existing CI runner's optional `--ai-core` flag combines selectors into its
**same pytest process**, deduplicating tests already covered by a selected file.
It includes the complete current request/recovery/evaluation test modules and
writes the separate100-case report from that same fresh XML. AI-core partial or
blocked evidence remains partial/blocked in `ci.json`; it is not transformed
into a passing readiness decision and cannot install approved evidence. The
existing Batch05 local-contract scope is not expanded into model-quality
certification. No second workflow or remote CI service was added.

Every user case has one of six statuses, and totals always account for100 cases:

- `passed`: all explicitly bound deterministic criteria passed in the declared
  scope. A component pass is labelled component; it does not certify the entire
  conversational capability or a native Office/desktop view.
- `partial`: a declared part or related regression passed, while the full user
  scenario, semantic judgment, visual review or automatic propagation is absent.
- `failed`: an executed bound assertion failed; a critical failure remains
  individually visible.
- `blocked`: no applicable fresh executor evidence exists or execution was
  incomplete. Absence is never a pass.
- `skipped`: a required test was explicitly skipped; it remains separate.
- `not_applicable`: reserved for a justified applicability decision. The runner
  does not use this status to hide missing dependencies.

`backend/ai_core_evaluation.py` owns the reviewed selector bindings and the
distinction between exact criterion tests and related tests. Test commands do
not come from dataset text. Passing a legacy or unrelated test cannot replace a
missing new-scenario test. Parameterized failures are not averaged away.

`AI_CORE_EVAL_RESULTS.json` records source, dataset and configuration fingerprints,
platform, Python version, command/exit/time,100 outcomes, independent sample and
explicitly unexecuted profiles. A change to code/docs during the run blocks
final comparability. `run-manifest.json` hashes the result/log/XML. These local
files are traceability, not cryptographic publisher approval. The separate
Batch05 release gate is not bypassed or silently updated.

Live model, independent semantic, rendered-visual and native desktop acceptance
remain separate profiles. Current local-contract results must not be described
as those tests. Exit0 requires all100 criteria passed in their declared scopes;
exit1 indicates a failed test/criterion; exit2 denotes incomplete or blocked
evidence. A deliberately honest partial run is therefore nonzero.
