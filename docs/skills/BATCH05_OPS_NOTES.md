# Batch05 operational foundations: 118–125

Environment measured: Windows11 AMD64, Python3.12.10, SQLite, existing Node24.19.0
and TypeScript. No Git remote or CI-provider configuration exists in this
checkout. No account, production data, cloud credential or external delivery
was used. The root agent wires the scoped APIs and UI; this file records the
underlying implementation/evidence without duplicating their final report.

| # | Implementation and existing component reused | Verified behavior / limit |
|---|---|---|
| 118 audit-events | `backend/skill_observability.py` over existing `guard_audit`; execution transitions call `record_event` in their transaction | Fixed event/field allowlists; no body, URL, filename, exception or secret fields. Collector failure propagates; revoked-session cleanup can record only fixed terminal states. Reads reauthorize owner/project/run. Retention is the existing local database lifetime, bounded reads200; no remote collector or claim of tamper-proof storage. |
| 119 tool-contract-testing | `tests/test_skill_batch05_contracts.py` exercises actual `skill_transform_protocol.decode_result` and existing async/result regressions | Wrong type/hash/reference/extra field/auth failure rejected; exact bytes and optional fields checked. A deliberately permissive adapter fails the contract assertion. This is an adapter test, not proof of a remote provider. |
| 120 ci-pipeline | `scripts/run_skill_regression.py`, existing pytest/TypeScript/Vite and locks | Fixed argv, isolated test data, no install/deploy/arbitrary command. Source change, missing report, skip or failure blocks acceptance. Actual TS2322 fixture failed, then numeric repair passed. Remote CI platform remains unconfigured/unexecuted. |
| 121 environment-variable-audit | `backend/environment_audit.py`, `scripts/audit_environment.py`, `.env.example`, startup hook | Names, source lines, configured flags and validity only. Invalid path/origin/model rejected; client secret reference detected synthetically. 41 literal launch/test names observed; dynamic accesses remain review-required. No secret rotation or configuration deletion. |
| 122 service-monitoring | `skill_observability.snapshot` over current tasks/SQLite/worker status | Scoped counts, oldest queue age and persisted total elapsed time. Stopped/stale/missing worker and storage failure are not green. Stale threshold is3 nominal5-second ticks, not a delivery SLA. Last200 task sample, p95 only≥100 measured terminal tasks. No outbound alerts. |
| 123 backup-disaster-recovery | Extended `backend/guard_backup.py`; legacy `scripts/backup_local.py` reuses validation | Actual current-user DPAPI SQLite snapshot and quarantined restore. Integrity, foreign keys, persisted-blob hashes and output references checked. Old grants/sessions/leases/workspace access disabled; potentially dispatched mail stays unknown. External workspaces/models/operator JSON/keyring excluded; no production activation or automatic deletion reconciliation. |
| 124 llm-evaluation-dataset | `evaluation/batch05/dataset.json`, `backend/skill_evaluation.py` | 24 synthetic routing cases, development12/holdout12, plus3 separate unexecuted semantic rubrics. Baseline24/24; no tuning/improvement claim. Canonical duplicates rejected; not complete semantic-duplicate detection or human-reviewed gold labels. |
| 125 skill-regression-suite | Fixed runner + registry agent's `skill_release` signature/gate | Versioned dataset and source fingerprint, actual pytest JUnit, explicit fail/skip/blocked counts; seeded routing/adapter/type defects detected. Local contract evidence never grants semantic/live-provider approval. `--install-evidence` is explicit and atomically updates the operator-local signed gate record only after successful requested checks. |

## Recorded tests and before/after

- [ops-before.xml](../../reports/skills-batch-05-20261116/ops-before.xml): **2 failed,4 passed**. A recovered durable reminder grant stayed valid, and a persisted blob with a false hash could be sealed. These are retained pre-fix failures.
- [ops-after.xml](../../reports/skills-batch-05-20261116/ops-after.xml): **7 passed**, including the existing DPAPI recovery regression.
- [ops-contracts-final-v3.xml](../../reports/skills-batch-05-20261116/ops-contracts-final-v3.xml): **25 passed**. Covers actual Windows DPAPI, revoked/missing resources, missing OS identity, hardlink refusal, missing saved output, audit/storage faults, contract mutations, signed evidence pointer and evaluation failures.
- [ci-type-mutation.xml](../../reports/skills-batch-05-20261116/ci-type-mutation.xml): **1 passed**; the test ran the installed TypeScript compiler on an isolated type error, observed TS2322, repaired the value and observed exit0. No product source was broken for this check.
- [trigger-baseline.json](../../reports/skills-batch-05-20261116/trigger-baseline.json): **24/24 routing cases passed before tuning**. [Frozen dataset bytes](../../reports/skills-batch-05-20261116/trigger-baseline-dataset.json) match its SHA. Semantic cases are outside the executable routing profile and not counted as passes.
- [environment-audit-final.json](../../reports/skills-batch-05-20261116/environment-audit-final.json):41 names,0 direct client-secret references, no values. Dynamic name accesses produce `review_required`; this is not a claim of exhaustive secret scanning.
- Full fixed CI and final UI evidence are produced by the root after code/docs freeze; do not infer their success from these component tests. Test groups overlap and must not be summed.

The initial restricted Windows DPAPI runs stopped making progress and were
interrupted without a completion claim. The recorded tests above ran with the
required Windows process/DPAPI access. There were no automatic-review denials.

## Actual recovery drill

[recovery-drill.json](../../reports/skills-batch-05-20261116/recovery-drill.json)
records a synthetic DOCX created/downloaded through the real authenticated
FastAPI task path, encrypted by actual Windows DPAPI, restored in a new quarantine
directory, then reopened as OOXML with matching SHA and expected text. The source
output was deleted after the snapshot; it stayed deleted in the source, while
the older restored copy remained quarantined and inaccessible to normal service
use. No permission or deletion reconciliation is falsely claimed.

One1MiB synthetic database measured seal0.047s, recover0.047s and entire
creation/download/recovery drill0.734s. These are one sample, not p95 or an RTO
guarantee. RTO/RPO production targets are **not agreed**; snapshots are manual,
with no backup schedule or cross-device key recovery promise. Original legacy
files that were never stored as blobs remain explicitly counted as unavailable
originals. The first drill completed validation but failed temporary-directory
cleanup due to an unclosed read-only SQLite handle; the handle was fixed, that
owned temporary directory was cleaned, and the recorded current drill passed.

Use the existing tool with a new destination:

```powershell
.venv/Scripts/python.exe -m backend.guard_backup seal C:/controlled/new.rwbackup
.venv/Scripts/python.exe -m backend.guard_backup recover C:/controlled/new.rwbackup C:/controlled/new-quarantine
```

The current Windows OS identity must be able to decrypt DPAPI. Recovery never
replaces active data. Do not serve or remove `RECOVERY_QUARANTINE` before offline
review of later deletions/access withdrawals/external effects and rebuilding
only approved derived indexes. Reconfigure operator-owned engine files and
OS-keyring credentials separately; do not copy sessions or permissions. The
legacy `backup_local.py` remains an explicitly unencrypted database export,
with the same integrity checks; it is not a production recovery certificate.

## Reproducible local pipeline and evidence

```powershell
.venv/Scripts/python.exe scripts/audit_environment.py --output reports/new-environment-audit.json
.venv/Scripts/python.exe scripts/run_skill_regression.py --ci --no-seal --output reports/new-batch05-ci
```

For the reviewed initialized **test/operator** data directory, omit `--no-seal`
and add `--install-evidence` to sign the fixed runner's own result and install
`skill-release/batch05-fast.json`. It archives the signed result, uses atomic
replacement, rejects linked destinations, preserves a prior pointer when the
new evidence fails, and cannot accept an arbitrary submitted JSON report to sign.
The existing protected key is read, not rotated or created. A subsequent source,
dataset or environment change invalidates the gate. This local acceptance does
not publish the product or lift the personal-memory/cloud/security gates.
