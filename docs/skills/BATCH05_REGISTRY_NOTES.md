# Registry, composition, package versions and release evidence — Batch05

This expands the existing manifest at `config/skills-batch-01.json` and the existing loader. It does not scan the filesystem for executable packages, install dependencies or create a second registry. Source-level controls are separate from SKILL.md frontmatter.

## 101: Catalog

`backend/skill_registry.py::validate_catalog` rejects duplicate names/numbers, missing descriptions, malformed version/hash/resource bindings, ambiguous aliases and malformed dependency contracts. Aliases point directly to a canonical registered name; they cannot shadow a real skill. Discovery returns independent `installed`, `enabled`, `ready`, `tested` and `tested_scope` fields. Missing/changed package bytes make `installed=false`; unknown runtime dependencies do not become available because a tool name exists. A server-provided operation probe must support every required operation. Activation is not an authorization grant. Current source bindings are re-read, so there is no stale discovery cache to invalidate.

The old catalog function checked names and fixed cardinality but accepted empty descriptions, `version=latest` and a non-hash `instructions_sha256=PASS`. [The same three synthetic manifests](../../reports/skills-batch-05-20261116/registry-before-after.json) were run against the preserved baseline function and the current validator: **0/3 correct rejections before, 3/3 now**. This is a deterministic replay of the captured baseline, not a live provider comparison or a pre-change timestamp invented after testing.

## 102: Dependencies

`resolve_dependencies` returns topological order, blockers and optional-unavailable records. Required missing/cyclic/incompatible/disabled dependencies prevent execution. An optional dependency with a broken required subtree is omitted as a whole; independent work remains eligible. Dependencies support exact numeric `major.minor.patch` and comma-separated `==`, `>=`, `<=`, `>` and `<` constraints. This is a deliberately bounded version grammar, not all npm/PEP440 syntax. The existing maximum of eight active skills applies after dependency expansion. Package resources are hash-bound and bounded; reading never executes scripts.

Runtime availability comes from trusted operation probes; filesystem installation alone is insufficient. Existing free-form dependency labels remain explicit lookup keys. Names beginning `optional-` retain the established optional meaning. No automatic downloads or local-to-cloud fallback are introduced.

## 103: Composition

`resolve_conflicts` keeps explicit language, format, output requirements, read-only and processing constraints above advisory package preferences. Different equal preferences without a user choice produce a bounded clarification. A cloud-required package under a local-only constraint is blocked. The decision record contains fields/reason codes, not model reasoning. Source files cannot supply these trusted constraints. Guard and typed tool boundaries remain authoritative; this is not a claim that prompt wording alone enforces security.

## 104: Trigger evaluation

The shared dataset/runner owned by the evaluation component covers 12 development and 12 held-out routing cases. It uses actual classification, selection and loading. See `evaluation/batch05/dataset.json` and the Batch05 evaluation report. No duplicate trigger dataset is maintained here. A 24/24 baseline does not justify claiming a measured routing improvement; mutant tests verify that the evaluator detects a broken route.

## 105: Versions

`backend/skill_pinning.py` captures the selected instructions and selected references in a bounded server-owned task pin. The existing `skill_runs.document` persists it. Public receipts expose versions, hashes and composition decisions, not instruction bodies. Pin bodies contain package instructions, never task documents, credentials or reasoning.

A compatible newly published version does not replace an in-flight task's instructions. Reusing the same version with a different registered hash is rejected. Missing/disabled/revoked packages, `revoked_versions` containing the pinned version, changed tool/input/output/egress/dependency contracts or corrupt pins block execution/resumption. This is a conservative compatibility rule, not an automatic migration. Resource bytes are pinned; a changed reference with the same name cannot silently replace them. Previous external effects are not undone by package rollback or revocation.

`skills.py` captures the pin before dispatch and rechecks it through the existing resource checks; translation loading receives the pin. No additional task engine or package-execution endpoint is introduced.

## 106: Release gate

`backend/skill_release.py` verifies one fixed-profile runner report. The trusted operator CLI invokes fixed tests and evaluation itself, then calls `seal_runner_report`; there is no HTTP signing endpoint and no CLI accepting arbitrary supplied PASS reports. Evidence is HMAC-bound to the installation's existing OS-protected master key and to the source fingerprint, Python/platform, dataset, case outcomes and runner profile. The key is not placed in the manifest, model prompt or report.

`evaluate_release` refuses unsigned/tampered evidence, stale source/environment, mismatched totals/profile, missing subprocess/holdout records, failed process exit, failed tests and skipped/blocked required checks. Every critical case is an independent veto. Local-contract approval never grants live-provider approval or publishing permission. A valid local signature proves this installation issued the record; it is not publisher identity, a human security review or a cryptographic guarantee that an operator-controlled machine is uncompromised.

Read-only diagnostics remain usable to explain a missing gate. Entries requiring `release_profile` are checked at activation; a global release decision is displayed separately. Existing capabilities are not silently relabeled fully tested merely because the new gate exists.

## Executed evidence and limits

* [registry-final-tests.xml](../../reports/skills-batch-05-20261116/registry-final-tests.xml): **38 passed in 2.67 seconds**, comprising 30 registry/pinning/gate cases and eight actual authenticated FastAPI/SQLite cases. Package fixtures and signing keys are synthetic; no network or service account is used. The earlier `registry-tests.xml` includes two existing loader/catalog regressions and is retained with its measured source checkpoint; do not add overlapping totals.
* [registry-api-tests.xml](../../reports/skills-batch-05-20261116/registry-api-tests.xml) retains the earlier three-case API checkpoint: persisted/public pin, revoked pending task rejection and compatible version retention. Five added cases in the final report inject a gated descriptor: missing, unsigned, stale or critical-failure evidence is rejected with HTTP422 before any skill run is created; valid synthetic server-signed fixture evidence permits the actual run. This tests the real gate boundary with a controlled issuer fixture, not an independently passing release test run. The fixed operator runner is evaluated separately.
* The first package-fixture run used Windows newline translation while hashing the LF string. It failed 11 cases before correcting the fixture to write the exact bytes; `registry-fixture-crlf-failure.xml` remains preserved. Those fixture failures are not counted as product improvement.
* Tests requiring Windows DPAPI/local service startup ran with authorized local test access after the restricted invocation stalled and was interrupted. No real credential was set and no user's profile was paired.

Reproduce with the project environment:

```powershell
.venv\Scripts\python.exe -X utf8 -B -m pytest tests/test_skill_batch05_registry.py tests/test_skill_batch05_registry_api.py -q
```

The final Batch05 acceptance report supersedes these checkpoint counts when subsequent integration cases are added. No native Windows post-pairing test, remote CI run, live provider test or external publish is claimed by this component.
