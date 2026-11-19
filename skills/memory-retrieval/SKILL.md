---
name: memory-retrieval
description: Retrieve relevant current project memory with a small context budget.
---

# استرجاع الذكريات المناسبة

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use current membership before ranking. Keep project memories separate from personal preferences. Respect corrections and temporary-chat constraints. Personal-memory injection remains gated; empty relevant results are valid.

Executor: `project-memory` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
