---
name: sandbox-execution
description: Run reviewed bounded checks in the existing isolated coding workspace.
---

# التنفيذ داخل بيئة معزولة

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use the coding check workflow with its granted workspace and pinned Docker image. No arbitrary shell or host fallback. Network, mounts, memory, processes and timeout must match the reviewed profile. Unknown effects are not replayed.

Executor: `coding-checks` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
