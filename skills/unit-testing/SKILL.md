---
name: unit-testing
description: Run meaningful scoped unit checks through the existing isolated runner.
---

# اختبارات الوحدات

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use reviewed Python or Node checks from a granted snapshot. Test behavior and boundaries, not implementation copies. Preserve failing assertions and dirty changes. Label mocks and exit status limits; stdout PASS is not trusted verification.

Executor: `coding-checks` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
