---
name: retrieval-permission-filter
description: Filter knowledge before it enters model context and recheck at delivery.
---

# تصفية المعرفة حسب الصلاحيات

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use Guard and current source snapshots before retrieval, again before publishing and opening evidence. Cache must carry owner/project/version/permission scope. Never retrieve forbidden excerpts and ask the model to ignore them.

Executor: `guard` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
