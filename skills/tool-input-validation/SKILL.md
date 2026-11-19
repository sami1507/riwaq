---
name: tool-input-validation
description: Validate runtime tool contracts before dispatch.
---

# التحقق من مدخلات الأدوات

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use strict schemas and resource IDs. Identity comes from the session. Reject unsafe paths and inconsistent fields before calling the executor. Validation does not grant permission.

Executor: `skill-contracts` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
