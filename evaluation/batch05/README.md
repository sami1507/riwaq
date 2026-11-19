# Foundation evaluation, version 2026-11-16.1

The dataset contains 24 deterministic routing/package cases (12 development,
12 holdout) and 3 separate semantic-review rubrics. All are synthetic; apparent
filenames are routing references only, not permissions or real private files.
The 24-case baseline passed before trigger tuning, so no routing improvement is
claimed. AR/colloquial AR, HE, EN and mixed labels are samples, not exhaustive
language certification. No model, provider, credentials or network is required.

Each deterministic case declares necessary/allowed skills and applicable
constraints, forbidden intents, multiple outputs or context-load bounds. There
may be several valid execution paths; permitted optional OCR does not require
OCR on an already readable file. The evaluator uses the real classifier,
selector, catalog and package loader. Its results establish routing only, not
tool effects or generated-answer truth. Independent pytest contracts cover the
actual adapters separately. Cases are never automatically rewritten after a
failure. Canonical duplicate input (including cross-split leakage) is rejected;
this catches normalization duplicates, not every semantic paraphrase.

Semantic rubrics explicitly accept multiple correct phrasings, uncertain
identity/numbers and a fluent answer that violates a no-send constraint. They
remain unexecuted and are not labeled human-reviewed gold answers. A separate
human review is needed to make claims about meaning and language generation.

## Local fixed CI profile

From the repository root, using a **new** output directory:

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/run_skill_regression.py --ci --no-seal --output reports/my-batch05-ci
```

The runner executes versioned cases and the trusted `tests/test_skill_batch05_*.py`
suite plus existing skill, lifecycle and Batch04 integration regressions with
the existing Python, then existing TypeScript and Vite. It installs
nothing, supplies an isolated test data directory, removes opt-in live-test
flags, executes argv without a shell, and has deadlines. Missing/skipped tests
are separate from success and block this required local profile. Tests use
synthetic data. pytest logs stay local; API diagnostics never serve their
contents. The inherited operator key is not touched with `--no-seal`.

Without `--no-seal`, the existing protected local identity signs evidence only
after this CLI executes the fixed profile. `release-evidence.json` is an operator
artifact; signing does not publish, enable a skill, or change runtime policy.
The release gate rechecks source fingerprints, profile, Python version, failures
and skips. Later code/dataset/contract changes invalidate old evidence. Keep the
result separate from semantic or real-service acceptance. A CI platform is not
configured because this checkout has no Git remote or existing CI provider;
these commands are the provider-neutral local pipeline. No deployment step or
`continue-on-error` exists.

Use `--install-evidence` only with the explicit initialized test/operator
`RIWAQ_DATA_DIR`. It requires sealing, successful requested checks and the current
source fingerprint; then it archives the signed evidence and atomically updates
`skill-release/batch05-fast.json` for the existing read-only gate. It does not
create a protected identity, publish a release or change cloud/permission gates.
An invalid, failed, stale or linked evidence destination is rejected. Omit this
flag for an ordinary measurement. CI on a remote platform remains unexecuted.
