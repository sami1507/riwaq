---
name: progressive-skill-loading
description: Load only selected reviewed skill packages and needed references.
---

# تحميل السكيل عند الحاجة

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Select from metadata first, then verify package hashes and dependency graph. Bound active packages and context; reject missing resources, cycles and name conflicts. Loading instructions does not execute scripts or grant their contents authority.

Executor: `skill-loader` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
