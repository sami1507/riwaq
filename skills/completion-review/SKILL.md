---
name: completion-review
description: Check all requested outputs against actual receipts before final delivery.
---

# مراجعة اكتمال المهمة

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Read persisted steps, criteria and outputs. Missing siblings make the whole request partial. Verify saved bytes and current authorization. Successful creation is distinct from semantic and visual acceptance. Do not weaken criteria.

Executor: `skill-lifecycle` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
